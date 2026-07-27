# © Copyright 2026 Aaron Kimball
"""`KlorbAcpAgent`: the ACP `Agent` implementation `AcpServer` runs -- negotiates the protocol,
owns the single live `Session`, and dispatches `session/new`/`session/prompt`/`session/cancel`.
See docs/specs/klorb-server.md."""

import asyncio
import logging
from pathlib import Path
from typing import Any, cast

import acp
from acp.helpers import update_current_mode
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
from klorb.permissions.directory_access import concat_dir_rules
from klorb.process_config import ProcessConfig, load_process_config
from klorb.server.turn_bridge import TurnBridge
from klorb.server.update_mapping import parse_session_config_update, session_config_json, session_mode_state
from klorb.session import Session
from klorb.session.constants import PermissionFramework
from klorb.tools.registry import ToolRegistry
from klorb.tools.skill.catalog import get_skill_catalog_registry
from klorb.tools.tasks.common import chainlink_available, chainlink_db_exists
from klorb.workspace import TrustManager, Workspace

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
        config_flag_path: Path | None = None,
    ) -> None:
        self._process_config = process_config
        self._provider = (
            provider if provider is not None
            else OpenRouterApiProvider(base_url=process_config.openrouter_base_url))
        self._model_registry = model_registry if model_registry is not None else ModelRegistry()
        self._trust_manager = trust_manager if trust_manager is not None else TrustManager()
        self._config_flag_path = config_flag_path
        """The `--config` path (if any) `klorb server`'s own CLI was started with -- threaded
        through to `load_process_config()` by `_apply_workspace_config()`, the same way
        `klorb.tui.ReplApp` keeps its own `_config_flag_path` for the same purpose."""
        self._client: acp.Client | None = None
        self._client_capabilities: ClientCapabilities | None = None
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
        self._client_capabilities = client_capabilities
        return acp.InitializeResponse(
            protocol_version=acp.PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(field_meta={"klorb": {
                "sessionConfig": True,
                "sessionStats": True,
                "trustWorkspace": True,
                "reloadSkills": True,
                "taskMeta": chainlink_available(),
            }}),
        )

    def _client_supports(self, flag: str) -> bool:
        """Whether the client advertised `clientCapabilities._meta.klorb.<flag>` at
        `initialize()` -- `False` if `initialize()` hasn't run yet, the client sent no
        `_meta`, or it omitted this specific flag."""
        if self._client_capabilities is None or self._client_capabilities.field_meta is None:
            return False
        klorb_meta = self._client_capabilities.field_meta.get("klorb")
        if not isinstance(klorb_meta, dict):
            return False
        return bool(klorb_meta.get(flag))

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
        self._turn_bridge = TurnBridge(
            session, self._require_client(), self._acp_session_id, self._process_config,
            raise_tool_call_limit_capable=self._client_supports("raiseToolCallLimit"),
            ask_user_questions_capable=self._client_supports("askUserQuestions"))
        logger.debug("session/new created ACP session %s for cwd=%s", self._acp_session_id, cwd)
        await self._maybe_send_initial_plan_snapshot(workspace.path)
        return acp.NewSessionResponse(
            session_id=self._acp_session_id,
            modes=session_mode_state(session.config.permission_framework),
            field_meta={"klorb": {
                "workspace": {"path": str(workspace.path), "trusted": workspace.trusted},
                "title": session.name,
            }},
        )

    async def _maybe_send_initial_plan_snapshot(self, workspace_path: Path) -> None:
        """Send one `plan` `session/update` for the just-built session's chainlink issues, but
        only if a `.chainlink/issues.db` already exists for `workspace_path` -- never triggers
        `chainlink init`'s scaffolding just to report an empty plan. A no-op if chainlink isn't
        available at all, or the fetch fails (`TurnBridge.fetch_plan_update` returns `None`).
        Runs the fetch off the event loop (`asyncio.to_thread`) since chainlink shells out
        synchronously -- see docs/specs/klorb-server.md's "Chainlink task-plan updates"
        section."""
        if not (chainlink_available() and chainlink_db_exists(workspace_path)):
            return
        assert self._turn_bridge is not None
        assert self._acp_session_id is not None
        plan_update = await asyncio.to_thread(self._turn_bridge.fetch_plan_update)
        if plan_update is not None:
            logger.debug(
                "session/new sending initial plan snapshot for ACP session %s", self._acp_session_id)
            await self._require_client().session_update(
                session_id=self._acp_session_id, update=plan_update)

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

    async def set_session_mode(
        self, mode_id: str, session_id: str, **kwargs: Any,
    ) -> SetSessionModeResponse | None:
        """Change the session's `permission_framework` to `mode_id` (one of `SESSION_MODES`'
        own ids) via `Session.set_permission_framework` -- so the model-facing interjection it
        queues is sent, not just the enforcement value -- then broadcast a `current_mode_update`
        confirming the change. Raises `invalid params` for a `mode_id` that isn't `"ask"`/
        `"auto"`/`"deny"` (`Session.set_permission_framework`'s own `ValueError`, translated to
        a JSON-RPC error rather than propagating a Python exception type across the wire)."""
        self._validate_session(session_id)
        assert self._session is not None
        try:
            self._session.set_permission_framework(cast(PermissionFramework, mode_id))
        except ValueError as exc:
            raise acp.RequestError.invalid_params({"modeId": mode_id, "reason": str(exc)}) from exc
        logger.debug("session/set_mode changed ACP session %s to %r", session_id, mode_id)
        await self._require_client().session_update(
            session_id=session_id, update=update_current_mode(mode_id))
        return SetSessionModeResponse()

    def _apply_workspace_config(self, workspace: Workspace) -> None:
        """Recompute the layered config now that `workspace`'s trust/registration state may
        have just changed, and apply it in place to the live session/process config --
        mirrors `klorb.tui.mixins.workspace_bootstrap.WorkspaceBootstrapMixin.
        _apply_workspace_config`, minus its TUI-only history-notice/header side effects. See
        `_klorb/trustWorkspace` in docs/specs/klorb-server.md."""
        assert self._session is not None
        reloaded = load_process_config(
            config_flag_path=self._config_flag_path, cwd=workspace.path, workspace=workspace)
        for warning in reloaded.config_warnings:
            if warning not in self._process_config.config_warnings:
                logger.warning(warning)
        for field_name in ProcessConfig.model_fields:
            # `session` is folded in separately below; `argv`/`cli_flags` are set once by
            # `klorb.cli.run_server_cli()` and never re-derived by `load_process_config()`, so
            # a reload would otherwise wipe them back to their empty defaults.
            if field_name in ("session", "argv", "cli_flags"):
                continue
            setattr(self._process_config, field_name, getattr(reloaded, field_name))

        self._session.config.workspace = workspace
        self._process_config.session.workspace = workspace

        self._session.config.read_dirs = concat_dir_rules(
            self._session.config.read_dirs, reloaded.session.read_dirs)
        self._session.config.write_dirs = concat_dir_rules(
            self._session.config.write_dirs, reloaded.session.write_dirs)
        self._process_config.session.read_dirs = concat_dir_rules(
            self._process_config.session.read_dirs, reloaded.session.read_dirs)
        self._process_config.session.write_dirs = concat_dir_rules(
            self._process_config.session.write_dirs, reloaded.session.write_dirs)

        get_skill_catalog_registry().reload(
            workspace_root=workspace.path, workspace_trusted=workspace.trusted,
            claude_skills_compat=self._process_config.compatibility_claude_skills)

    def _require_session_id(self, params: dict[str, Any]) -> str:
        """Extract and validate `params["sessionId"]` for a `_klorb/*` ext request, raising
        `invalid params` for a missing/non-string/unknown session id."""
        session_id = params.get("sessionId")
        if not isinstance(session_id, str):
            raise acp.RequestError.invalid_params({"reason": "sessionId is required"})
        self._validate_session(session_id)
        return session_id

    def _ext_get_session_config(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_session_id(params)
        assert self._session is not None
        return session_config_json(self._session, self._model_registry)

    def _ext_set_session_config(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_session_id(params)
        assert self._session is not None
        update = parse_session_config_update(params)
        if update.model is not None:
            self._session.config.model = update.model
        if update.thinking is not None:
            if update.thinking.enabled is not None:
                self._session.config.thinking_enabled = update.thinking.enabled
            if update.thinking.effort is not None:
                self._session.config.thinking_effort = update.thinking.effort
        logger.debug(
            "_klorb/setSessionConfig applied for ACP session %s: %r", self._acp_session_id, update)
        return session_config_json(self._session, self._model_registry)

    def _ext_session_stats(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_session_id(params)
        assert self._session is not None
        return self._session.statistics.model_dump(mode="json")

    def _ext_reload_skills(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_session_id(params)
        assert self._session is not None
        workspace = self._session.config.workspace
        catalogs = get_skill_catalog_registry().reload(
            workspace_root=workspace.path, workspace_trusted=workspace.trusted,
            claude_skills_compat=self._process_config.compatibility_claude_skills)
        logger.debug(
            "_klorb/reloadSkills rebuilt the skill catalog for ACP session %s: %d skill(s)",
            self._acp_session_id, len(catalogs.canonical))
        return {"skillCount": len(catalogs.canonical)}

    def _ext_trust_workspace(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_session_id(params)
        assert self._session is not None
        workspace = self._session.config.workspace
        trusted_workspace = workspace.model_copy(update={"trusted": True})
        if trusted_workspace.is_project:
            assert trusted_workspace.id is not None
            self._trust_manager.set_trusted(trusted_workspace.id, True)
        self._apply_workspace_config(trusted_workspace)
        logger.debug(
            "_klorb/trustWorkspace trusted %s for ACP session %s",
            trusted_workspace.path, self._acp_session_id)
        return {"workspace": {"path": str(trusted_workspace.path), "trusted": trusted_workspace.trusted}}

    # -- Protocol surface not implemented at this checkpoint --
    #
    # `acp.Agent` is a `Protocol`; explicitly subclassing one (as `KlorbAcpAgent` does, for a
    # concrete, type-checked implementation) requires overriding every member, so mypy treats
    # each unimplemented one as abstract. None of these are ever dispatched by a compliant
    # client: `initialize()` never advertises `loadSession`/auth methods/model selection
    # (`session/set_model` stays unimplemented -- see `session_config_json`'s docstring for why
    # model selection rides `_klorb/setSessionConfig` instead)/fork/resume -- rejecting
    # explicitly here, rather than an inherited no-op returning `None`, is what a client would
    # see if it tried anyway.

    async def load_session(
        self, cwd: str, mcp_servers: list[_McpServerSpec], session_id: str, **kwargs: Any,
    ) -> LoadSessionResponse | None:
        raise acp.RequestError.method_not_found("session/load")

    async def list_sessions(
        self, cursor: str | None = None, cwd: str | None = None, **kwargs: Any,
    ) -> ListSessionsResponse:
        raise acp.RequestError.method_not_found("session/list")

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
        if method == "klorb/getSessionConfig":
            return self._ext_get_session_config(params)
        if method == "klorb/setSessionConfig":
            return self._ext_set_session_config(params)
        if method == "klorb/sessionStats":
            return self._ext_session_stats(params)
        if method == "klorb/reloadSkills":
            return self._ext_reload_skills(params)
        if method == "klorb/trustWorkspace":
            return self._ext_trust_workspace(params)
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
