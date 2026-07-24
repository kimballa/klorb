# © Copyright 2026 Aaron Kimball
"""`KlorbAcpAgent`: the ACP `Agent` implementation `AcpServer` runs -- negotiates the protocol,
owns the single live `Session`, and dispatches `session/new`/`session/prompt`/`session/cancel`.
See docs/specs/klorb-server.md."""

import logging
from pathlib import Path
from typing import Any

import acp
from acp.schema import (
    AgentCapabilities,
    AudioContentBlock,
    AuthenticateResponse,
    ClientCapabilities,
    EmbeddedResourceContentBlock,
    ForkSessionResponse,
    HttpMcpServer,
    ImageContentBlock,
    Implementation,
    ListSessionsResponse,
    LoadSessionResponse,
    McpServerStdio,
    ResourceContentBlock,
    ResumeSessionResponse,
    SetSessionModelResponse,
    SetSessionModeResponse,
    SseMcpServer,
    TextContentBlock,
)

from klorb.api_provider import ApiProvider, ResponseAborted
from klorb.models.registry import ModelRegistry
from klorb.openrouter import OpenRouterApiProvider
from klorb.process_config import ProcessConfig
from klorb.server.turn_bridge import TurnBridge
from klorb.session import Session
from klorb.tools.registry import ToolRegistry
from klorb.workspace import TrustManager

logger = logging.getLogger(__name__)

_PromptContentBlock = (
    TextContentBlock | ImageContentBlock | AudioContentBlock | ResourceContentBlock
    | EmbeddedResourceContentBlock
)
_McpServerSpec = HttpMcpServer | SseMcpServer | McpServerStdio


class KlorbAcpAgent(acp.Agent):
    """Implements the ACP `Agent` protocol on top of a single live `Session`.

    Owns at most one `Session` at a time: `new_session()` tears down (`Session.close()`) and
    replaces any prior one, the same `/clear` semantics the interactive TUI already has -- see
    docs/specs/klorb-server.md's "Single top-level session" section. The `ApiProvider`/
    `ModelRegistry` outlive any one `Session` and are reused across every replacement, mirroring
    how [[terminal-repl]]'s `/clear` reuses `Session.provider`/`Session.model_registry`.

    The ACP `sessionId` handed back to the client from `new_session()` is snapshotted into
    `self._acp_session_id` and never re-read from the live `Session.id` afterward: `Session.id`
    is renamed in place by the session-naming classifier on the session's first turn (see
    `klorb.session.mixins.core.SessionCoreMixin._run_session_naming`), but the identity a
    client keeps addressing `session/prompt`/`session/cancel` requests to must stay fixed for
    the session's lifetime.
    """

    def __init__(
        self,
        process_config: ProcessConfig,
        provider: ApiProvider | None = None,
        model_registry: ModelRegistry | None = None,
        trust_manager: TrustManager | None = None,
    ) -> None:
        self._process_config = process_config
        self._provider = (
            provider if provider is not None
            else OpenRouterApiProvider(base_url=process_config.openrouter_base_url))
        self._model_registry = model_registry if model_registry is not None else ModelRegistry()
        self._trust_manager = trust_manager if trust_manager is not None else TrustManager()
        self._client: acp.Client | None = None
        self._session: Session | None = None
        self._acp_session_id: str | None = None
        self._turn_bridge: TurnBridge | None = None
        self._turn_in_flight = False

    @property
    def session(self) -> Session | None:
        """The live `Session`, or `None` before the first `session/new` request -- exposed
        read-only for a test harness to inspect (e.g. `session.messages`) without a separate
        injection seam."""
        return self._session

    def on_connect(self, conn: acp.Client) -> None:
        """Called once by the SDK connection right after construction, handing back the
        `Client` proxy this agent uses for every outbound call (`session/update`, ...) for the
        rest of the connection's lifetime."""
        logger.debug("ACP client connected")
        self._client = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> acp.InitializeResponse:
        logger.debug(
            "ACP initialize: client protocolVersion=%s clientInfo=%s", protocol_version, client_info)
        return acp.InitializeResponse(
            protocol_version=acp.PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(field_meta={"klorb": {}}),
        )

    async def new_session(
        self, cwd: str, mcp_servers: list[_McpServerSpec], **kwargs: Any,
    ) -> acp.NewSessionResponse:
        """Build a fresh `Session` for `cwd`, replacing any live one -- see class docstring.
        `mcp_servers` is accepted but never acted on: klorb has no MCP support."""
        workspace = self._trust_manager.resolve_workspace(Path(cwd))
        session_config = self._process_config.session.model_copy()
        session_config.workspace = workspace
        tool_registry = ToolRegistry.discover_tools(self._process_config, session_config)
        if self._session is not None:
            logger.debug("session/new replacing live ACP session %s", self._acp_session_id)
            self._session.close()
        session = Session(
            session_config, provider=self._provider, model_registry=self._model_registry,
            process_config=self._process_config, tool_registry=tool_registry)
        self._session = session
        self._acp_session_id = session.id
        self._turn_bridge = TurnBridge(session, self._require_client(), self._acp_session_id)
        logger.debug("session/new created ACP session %s for cwd=%s", self._acp_session_id, cwd)
        return acp.NewSessionResponse(session_id=self._acp_session_id)

    async def prompt(
        self, prompt: list[_PromptContentBlock], session_id: str, **kwargs: Any,
    ) -> acp.PromptResponse:
        self._validate_session(session_id)
        if self._turn_in_flight:
            raise acp.RequestError(-32000, "A prompt is already in progress for this session")
        prompt_text = _extract_prompt_text(prompt)
        assert self._turn_bridge is not None
        self._turn_in_flight = True
        logger.debug("session/prompt dispatching turn for ACP session %s", session_id)
        try:
            await self._turn_bridge.run_turn(prompt_text)
        except ResponseAborted:
            logger.debug("session/prompt turn cancelled for ACP session %s", session_id)
            return acp.PromptResponse(stop_reason="cancelled")
        finally:
            self._turn_in_flight = False
        return acp.PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        """Set the active turn's cancel event, if one is running for `session_id` -- a no-op
        otherwise (unknown session, or no turn in flight)."""
        if self._session is None or session_id != self._acp_session_id:
            return
        cancel_event = self._session.active_cancel_event
        if cancel_event is not None:
            logger.debug("session/cancel signaling in-flight turn for ACP session %s", session_id)
            cancel_event.set()

    def close(self) -> None:
        """Close any live session -- called by `AcpServer.run()` once the client disconnects."""
        if self._session is not None:
            logger.debug("Closing live ACP session %s on server shutdown", self._acp_session_id)
            self._session.close()
            self._session = None

    def _validate_session(self, session_id: str) -> None:
        if self._session is None or session_id != self._acp_session_id:
            raise acp.RequestError.invalid_params({"sessionId": session_id, "reason": "unknown session"})

    def _require_client(self) -> acp.Client:
        if self._client is None:
            raise RuntimeError("KlorbAcpAgent.new_session called before on_connect")
        return self._client

    # -- Protocol surface not implemented at this checkpoint --
    #
    # `acp.Agent` is a `Protocol`; explicitly subclassing one (as `KlorbAcpAgent` does, for a
    # concrete, type-checked implementation) requires overriding every member, so mypy treats
    # each unimplemented one as abstract. None of these are ever dispatched by a compliant
    # client: `initialize()` never advertises `loadSession`/auth methods/session modes/model
    # selection/fork/resume, and `_klorb/*` extension methods don't exist yet (see the "no
    # standard fit" ext-method rows in the plan overview) -- rejecting explicitly here, rather
    # than an inherited no-op returning `None`, is what a client would see if it tried anyway.

    async def load_session(
        self, cwd: str, mcp_servers: list[_McpServerSpec], session_id: str, **kwargs: Any,
    ) -> LoadSessionResponse | None:
        raise acp.RequestError.method_not_found("session/load")

    async def list_sessions(
        self, cursor: str | None = None, cwd: str | None = None, **kwargs: Any,
    ) -> ListSessionsResponse:
        raise acp.RequestError.method_not_found("session/list")

    async def set_session_mode(
        self, mode_id: str, session_id: str, **kwargs: Any,
    ) -> SetSessionModeResponse | None:
        raise acp.RequestError.method_not_found("session/set_mode")

    async def set_session_model(
        self, model_id: str, session_id: str, **kwargs: Any,
    ) -> SetSessionModelResponse | None:
        raise acp.RequestError.method_not_found("session/set_model")

    async def authenticate(self, method_id: str, **kwargs: Any) -> AuthenticateResponse | None:
        raise acp.RequestError.method_not_found("authenticate")

    async def fork_session(
        self, cwd: str, session_id: str, mcp_servers: list[_McpServerSpec] | None = None, **kwargs: Any,
    ) -> ForkSessionResponse:
        raise acp.RequestError.method_not_found("session/fork")

    async def resume_session(
        self, cwd: str, session_id: str, mcp_servers: list[_McpServerSpec] | None = None, **kwargs: Any,
    ) -> ResumeSessionResponse:
        raise acp.RequestError.method_not_found("session/resume")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise acp.RequestError.method_not_found(f"_{method}")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        """Unrecognized extension notifications are ignored, per the ACP extensibility rules
        klorb follows -- see the plan overview's "Extensibility rules" section."""


def _extract_prompt_text(blocks: list[_PromptContentBlock]) -> str:
    """Concatenate every `text` block's content, in order. Raises a JSON-RPC `invalid params`
    error on the first non-text block -- images/audio/resources aren't supported until a later
    increment."""
    texts: list[str] = []
    for block in blocks:
        if block.type != "text":
            raise acp.RequestError.invalid_params(
                {"reason": f"content block type {block.type!r} is not supported yet"})
        texts.append(block.text)
    return "".join(texts)
