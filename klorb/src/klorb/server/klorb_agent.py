# © Copyright 2026 Aaron Kimball
"""`KlorbAcpAgent`: the ACP `Agent` implementation `AcpServer` runs -- negotiates the protocol,
owns the single live `Session`, and dispatches `session/new`/`session/prompt`/`session/cancel`.
See docs/specs/klorb-server.md."""

import asyncio
import base64
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
    SessionCapabilities,
    SessionInfo,
    SessionListCapabilities,
    SetSessionModelResponse,
    SetSessionModeResponse,
    SseMcpServer,
    TextContentBlock,
)

from klorb.agents.policy import compute_root_session_grants, dispatch_direct_message
from klorb.agents.runtime import SUBAGENT_ABORTED_MARKER, SubagentHandle, walk_session_tree
from klorb.api_provider import ApiProvider, ResponseAborted
from klorb.images.prepare import ImagePipelineConfig, ImageTooLargeError, prepare_image_for_model
from klorb.message import MessageFragment
from klorb.models.model import Model
from klorb.models.registry import ModelRegistry
from klorb.openrouter import OpenRouterApiProvider
from klorb.permissions.directory_access import concat_dir_rules
from klorb.process_config import ProcessConfig, load_process_config, persist_session_default, user_config_path
from klorb.server.subagent_updates import build_subagent_tree_snapshot
from klorb.server.turn_bridge import TurnBridge
from klorb.server.update_mapping import (
    build_session_replay,
    parse_session_config_update,
    session_config_json,
    session_mode_state,
)
from klorb.session import Session
from klorb.session.constants import PermissionFramework, ToolCallLimitExceeded
from klorb.session.events import QueuedMessage
from klorb.session.restore import try_restore_session
from klorb.tools.exceptions import ToolCallError
from klorb.tools.tasks.common import chainlink_available, chainlink_db_exists
from klorb.workspace import TrustManager, Workspace
from klorb.workspace.session_store import find_recent_session, read_sessions_index

logger = logging.getLogger(__name__)

_PromptContentBlock = (
    TextContentBlock | ImageContentBlock | AudioContentBlock | ResourceContentBlock
    | EmbeddedResourceContentBlock
)
_McpServerSpec = HttpMcpServer | SseMcpServer | McpServerStdio

_NOTICE_EXT_NOTIFICATION = "klorb/notice"
"""The `_klorb/notice` extension notification name, with the leading `_` stripped. Sent
unconditionally (no capability gate), same as every other `_klorb/*` extension notification.
Carries one hook firing's `HookOutput.log`."""


class KlorbAcpAgent(acp.Agent):
    """Implements the ACP `Agent` protocol on top of a single live `Session`.

    Owns at most one `Session` at a time: `new_session()`/`load_session()` each tear down
    (`Session.close()`) and replace any prior one. The `ApiProvider`/`ModelRegistry` outlive
    any one `Session` and are reused across every replacement.

    The ACP `sessionId` handed back to the client from `new_session()`/`load_session()` is
    `self._session.id`.
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
        through to `load_process_config()` by `_apply_workspace_config()`."""
        self._client: acp.Client | None = None
        self._client_capabilities: ClientCapabilities | None = None
        self._session: Session | None = None
        self._turn_bridge: TurnBridge | None = None
        self._turn_in_flight = False

    @property
    def session(self) -> Session | None:
        """The live `Session`, or `None` before the first `session/new` request -- exposed
        read-only for a test harness to inspect without a separate injection seam."""
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
            agent_capabilities=AgentCapabilities(
                load_session=True,
                session_capabilities=SessionCapabilities(list=SessionListCapabilities()),
                field_meta={"klorb": {
                    "sessionConfig": True,
                    "sessionStats": True,
                    "trustWorkspace": True,
                    "reloadSkills": True,
                    "enqueueMessage": True,
                    "setSessionTitle": True,
                    "taskMeta": chainlink_available(),
                    "imageInput": True,
                    "subagents": True,
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
        """Build a fresh `Session` for `cwd`, replacing any live one.
        `mcp_servers` is accepted but never acted on: klorb has no MCP support."""
        workspace = self._trust_manager.resolve_workspace(Path(cwd))
        session_config = self._process_config.session.model_copy()
        session_config.apply_workspace_access(
            workspace=workspace, read_dirs=session_config.read_dirs,
            write_dirs=session_config.write_dirs)
        grants = compute_root_session_grants(self._process_config, session_config, session_config.role_name)
        session_config.skill_rules = grants.skill_rules
        if self._session is not None:
            logger.debug("session/new replacing live ACP session %s", self._session.id)
            self._session.close()
        session = Session(
            session_config, provider=self._provider, model_registry=self._model_registry,
            process_config=self._process_config, tool_registry=grants.tool_registry,
            effective_subagent_roles=grants.effective_subagent_roles)
        self._wire_session_notice_handler(session)
        self._wire_session_wake_handler(session)
        session.fire_session_start_hook("NewSession")
        self._session = session
        self._turn_bridge = TurnBridge(
            session, self._require_client(), session.id, self._process_config,
            raise_tool_call_limit_capable=self._client_supports("raiseToolCallLimit"),
            ask_user_questions_capable=self._client_supports("askUserQuestions"))
        logger.debug("session/new created ACP session %s for cwd=%s", session.id, cwd)
        await self._maybe_send_initial_plan_snapshot(workspace.path)
        return acp.NewSessionResponse(
            session_id=session.id,
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
        available at all, or the fetch fails. Runs the fetch off the event loop since chainlink
        shells out synchronously."""
        if not (chainlink_available() and chainlink_db_exists(workspace_path)):
            return
        assert self._turn_bridge is not None
        assert self._session is not None
        plan_update = await asyncio.to_thread(self._turn_bridge.fetch_plan_update)
        if plan_update is not None:
            logger.debug(
                "session/new sending initial plan snapshot for ACP session %s", self._session.id)
            await self._require_client().session_update(
                session_id=self._session.id, update=plan_update)

    async def prompt(
        self, prompt: list[_PromptContentBlock], session_id: str, **kwargs: Any,
    ) -> acp.PromptResponse:
        """Dispatch one turn, then persist the session's resulting state (`Session.
        persist_state()` -- a no-op for an untrusted workspace, or if the session never
        successfully claimed a directory) whether the turn finished normally or was cancelled,
        so a saved session stays current without requiring an explicit save request."""
        self._validate_session(session_id)
        if self._turn_in_flight:
            raise acp.RequestError(-32000, "A prompt is already in progress for this session")
        assert self._session is not None
        prompt_text, image_fragments = _extract_prompt_content(
            prompt, self._session.active_model(), self._session.image_pipeline_config)
        assert self._turn_bridge is not None
        self._turn_in_flight = True
        logger.debug("session/prompt dispatching turn for ACP session %s", session_id)
        try:
            await self._turn_bridge.run_turn(prompt_text, image_fragments=image_fragments)
        except ResponseAborted:
            logger.debug("session/prompt turn cancelled for ACP session %s", session_id)
            self._session.persist_state()
            return acp.PromptResponse(stop_reason="cancelled")
        except ToolCallLimitExceeded:
            raise
        except Exception as exc:
            logger.error(
                "session/prompt turn failed for ACP session %s: %s", session_id, exc,
                exc_info=True,
            )
            try:
                await self._require_client().session_update(
                    session_id=session_id,
                    update=acp.update_agent_message_text(
                        f"\n\n[Provider error: {exc}]"))
            except Exception:
                logger.debug(
                    "Failed to send provider-error update to ACP client for session %s",
                    session_id, exc_info=True)
            self._session.persist_state()
            return acp.PromptResponse(stop_reason="refusal")
        finally:
            self._turn_in_flight = False
        self._session.persist_state()
        return acp.PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        """Latch a cancellation request on the turn bridge for `session_id`. A no-op if the
        session is unknown or no turn is in flight."""
        if self._session is None or session_id != self._session.id or not self._turn_in_flight:
            return
        assert self._turn_bridge is not None
        logger.debug("session/cancel signaling in-flight turn for ACP session %s", session_id)
        self._turn_bridge.request_cancel()

    def close(self) -> None:
        """Close any live session."""
        if self._session is not None:
            logger.debug("Closing live ACP session %s on server shutdown", self._session.id)
            self._session.close()
            self._session = None

    def _validate_session(self, session_id: str) -> None:
        if self._session is None or session_id != self._session.id:
            raise acp.RequestError.invalid_params({"sessionId": session_id, "reason": "unknown session"})

    def _require_client(self) -> acp.Client:
        if self._client is None:
            raise RuntimeError("KlorbAcpAgent.new_session called before on_connect")
        return self._client

    def _wire_session_notice_handler(self, session: Session) -> None:
        """Register this agent's ACP client as `session`'s `HookOutput.log` notice sink, sending
        a `_klorb/notice` extension notification."""
        loop = asyncio.get_running_loop()

        def notify(text: str) -> None:
            asyncio.run_coroutine_threadsafe(self._send_notice(session.id, text), loop)

        session.register_notice_handler(notify)

    def _wire_session_wake_handler(self, session: Session) -> None:
        """Register this agent as `session`'s wake handler: a
        `Timer`/`FileSystemModified`/`WorkspaceTrustChanged` event that queues a message while
        no turn is in flight wakes this agent to drain and resubmit it via `TurnBridge.run_turn`."""
        loop = asyncio.get_running_loop()

        def wake() -> None:
            asyncio.run_coroutine_threadsafe(self._drain_and_submit_woken_turn(session.id), loop)

        session.register_wake_handler(wake)

    async def _drain_and_submit_woken_turn(self, session_id: str) -> None:
        """Handles a wake ping: drains whatever an
        idle-triggered event or `reset_session` just queued and resubmits it via `TurnBridge.
        run_turn`."""
        if self._turn_in_flight or self._session is None or self._session.id != session_id:
            return
        text = self._session.drain_next_turn_text()
        if text is None:
            return
        assert self._turn_bridge is not None
        self._turn_in_flight = True
        try:
            await self._turn_bridge.run_turn(text)
        except ResponseAborted:
            logger.debug("Woken turn cancelled for ACP session %s", session_id)
        except Exception as exc:
            logger.error(
                "Woken turn failed for ACP session %s: %s", session_id, exc, exc_info=True)
            try:
                await self._require_client().session_update(
                    session_id=session_id,
                    update=acp.update_agent_message_text(f"\n\n[Provider error: {exc}]"))
            except Exception:
                logger.debug(
                    "Failed to send provider-error update to ACP client for session %s",
                    session_id, exc_info=True)
        finally:
            self._turn_in_flight = False
        self._session.persist_state()

    async def _send_notice(self, session_id: str, text: str) -> None:
        """Send one `_klorb/notice` extension notification, best-effort -- a failure
        is logged at `debug` rather than raised, since nothing is
        awaiting this fire-and-forget call's outcome."""
        try:
            await self._require_client().ext_notification(
                _NOTICE_EXT_NOTIFICATION, {"sessionId": session_id, "text": text})
        except Exception:
            logger.debug(
                "Failed to send _klorb/notice for ACP session %s", session_id, exc_info=True)

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
        have just changed, and apply it in place to the live session/process config."""
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

        self._session.config.apply_workspace_access(
            workspace=workspace,
            read_dirs=concat_dir_rules(self._session.config.read_dirs, reloaded.session.read_dirs),
            write_dirs=concat_dir_rules(self._session.config.write_dirs, reloaded.session.write_dirs))
        self._process_config.session.apply_workspace_access(
            workspace=workspace,
            read_dirs=concat_dir_rules(
                self._process_config.session.read_dirs, reloaded.session.read_dirs),
            write_dirs=concat_dir_rules(
                self._process_config.session.write_dirs, reloaded.session.write_dirs))

        self._session.reload_skills()

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
            self._process_config.session.model = update.model
            persist_session_default(user_config_path(), "model", update.model)
        if update.thinking is not None:
            if update.thinking.enabled is not None:
                self._session.config.thinking_enabled = update.thinking.enabled
                self._process_config.session.thinking_enabled = update.thinking.enabled
                persist_session_default(user_config_path(), "thinking.enabled", update.thinking.enabled)
            if update.thinking.effort is not None:
                self._session.config.thinking_effort = update.thinking.effort
                self._process_config.session.thinking_effort = update.thinking.effort
                persist_session_default(user_config_path(), "thinking.effort", update.thinking.effort)
        logger.debug(
            "_klorb/setSessionConfig applied for ACP session %s: %r", self._session.id, update)
        return session_config_json(self._session, self._model_registry)

    def _ext_set_session_title(self, params: dict[str, Any]) -> dict[str, Any]:
        """Apply a user-driven session rename.
        """
        self._require_session_id(params)
        assert self._session is not None
        title = params.get("title")
        if title is not None and not isinstance(title, str):
            raise acp.RequestError.invalid_params({"reason": "title must be a string or null"})
        self._session.name = title.strip() if isinstance(title, str) and title.strip() else None
        self._session.session_naming_pending = False
        self._session.cancel_session_naming()
        self._session.persist_state()
        logger.debug(
            "_klorb/setSessionTitle applied for ACP session %s: %r",
            self._session.id, self._session.name)
        return {"title": self._session.name}

    def _ext_session_stats(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_session_id(params)
        assert self._session is not None
        return self._session.statistics.model_dump(mode="json")

    def _ext_reload_skills(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_session_id(params)
        assert self._session is not None
        catalogs = self._session.reload_skills()
        logger.debug(
            "_klorb/reloadSkills rebuilt the skill catalog for ACP session %s: %d skill(s)",
            self._session.id, len(catalogs.canonical))
        return {"skillCount": len(catalogs.canonical)}

    def _ext_list_skills(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_session_id(params)
        assert self._session is not None
        skills = self._session.discover_skills()
        entries = [
            {"namespace": skill.namespace, "name": skill.name, "description": skill.description}
            for skill in skills
        ]
        logger.debug(
            "_klorb/listSkills returning %d skill(s) for ACP session %s",
            len(entries), self._session.id)
        return {"skills": entries}

    def _ext_enqueue_message(self, params: dict[str, Any]) -> dict[str, Any]:
        """Queue `params["text"]` as a `QueuedMessage` for delivery into the running turn --
        see `_klorb/enqueueMessage` in docs/specs/klorb-server.md. Only valid while a
        `session/prompt` is in flight for this session; a client with nothing running should
        send an ordinary `session/prompt` instead."""
        self._require_session_id(params)
        if not self._turn_in_flight:
            raise acp.RequestError(-32000, "No prompt is in progress for this session")
        text = params.get("text")
        if not isinstance(text, str):
            raise acp.RequestError.invalid_params({"reason": "text is required"})
        assert self._session is not None
        self._session.enqueue_queued_message(QueuedMessage(message_text=text))
        logger.debug(
            "_klorb/enqueueMessage queued a message for ACP session %s", self._session.id)
        return {"queued": True}

    def _find_subagent(self, subagent_id: str) -> tuple[Session, SubagentHandle] | None:
        """Look up `subagent_id` (a non-root `Session.id`) anywhere in the live tree, returning
        its `Session` and the `SubagentHandle` its creator tracks it under, or `None` if no node
        in the tree (other than the root, which has no handle) matches."""
        assert self._session is not None
        for node in walk_session_tree(self._session):
            if node.session.id == subagent_id and node.handle is not None:
                return node.session, node.handle
        return None

    def _ext_subagent_tree(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_session_id(params)
        assert self._session is not None
        return build_subagent_tree_snapshot(self._session)

    def _ext_subagent_transcript(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_session_id(params)
        subagent_id = params.get("subagentId")
        if not isinstance(subagent_id, str):
            raise acp.RequestError.invalid_params({"reason": "subagentId is required"})
        found = self._find_subagent(subagent_id)
        if found is None:
            raise acp.RequestError.invalid_params(
                {"subagentId": subagent_id, "reason": "unknown subagent"})
        subagent_session, handle = found
        assert self._session is not None
        entries = build_session_replay(
            subagent_session, subagent_session.tool_registry, self._session.config.workspace.path)
        return {
            "entries": entries,
            "state": handle.state,
            "aborted": SUBAGENT_ABORTED_MARKER in (handle.output or ""),
            "queuedMessages": subagent_session.pending_queued_message_texts,
        }

    def _ext_subagent_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_session_id(params)
        subagent_id = params.get("subagentId")
        if not isinstance(subagent_id, str):
            raise acp.RequestError.invalid_params({"reason": "subagentId is required"})
        found = self._find_subagent(subagent_id)
        if found is None:
            raise acp.RequestError.invalid_params(
                {"subagentId": subagent_id, "reason": "unknown subagent"})
        _, handle = found
        handle.cancel_event.set()
        logger.debug("_klorb/subagentCancel signaled cancel_event for subagent %s", subagent_id)
        return {"cancelled": True}

    def _ext_subagent_prompt(self, params: dict[str, Any]) -> dict[str, Any]:
        """Send `params["text"]` from a human user directly to a subagent. `mode` comes back
        from `dispatch_direct_message`, which resolves it under the tracker's dispatch guard
        rather than this handler guessing from a possibly-stale `handle.state` read."""
        self._require_session_id(params)
        subagent_id = params.get("subagentId")
        if not isinstance(subagent_id, str):
            raise acp.RequestError.invalid_params({"reason": "subagentId is required"})
        text = params.get("text")
        if not isinstance(text, str) or not text:
            raise acp.RequestError.invalid_params({"reason": "text is required"})
        found = self._find_subagent(subagent_id)
        if found is None:
            raise acp.RequestError.invalid_params(
                {"subagentId": subagent_id, "reason": "unknown subagent"})
        child, handle = found
        try:
            mode = dispatch_direct_message(self._process_config, child, handle, text)
        except ToolCallError as exc:
            raise acp.RequestError(-32000, str(exc)) from exc
        logger.debug("_klorb/subagentPrompt %s a message for subagent %s", mode, subagent_id)
        return {"mode": mode}

    def _ext_trust_workspace(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_session_id(params)
        assert self._session is not None
        workspace = self._session.config.workspace
        trusted_workspace = workspace.model_copy(update={"trusted": True})
        if trusted_workspace.is_project:
            assert trusted_workspace.id is not None
            self._trust_manager.set_trusted(trusted_workspace.id, True)
        self._apply_workspace_config(trusted_workspace)
        self._session.fire_workspace_trust_changed_hook("AcpTrustWorkspace")
        logger.debug(
            "_klorb/trustWorkspace trusted %s for ACP session %s",
            trusted_workspace.path, self._session.id)
        return {"workspace": {"path": str(trusted_workspace.path), "trusted": trusted_workspace.trusted}}

    async def load_session(
        self, cwd: str, mcp_servers: list[_McpServerSpec], session_id: str, **kwargs: Any,
    ) -> LoadSessionResponse | None:
        """Replace the live session (if any) with the one recorded under `session_id` in
        `cwd`'s workspace `sessions.json`. Raises `invalid_params` -- rather than restoring
        nothing and silently starting fresh -- if `session_id` isn't in the index at all, or its
        `sessions/<subdir>/` directory is currently locked by another live process or its
        `session.json` is missing/corrupt (`try_restore_session` returns `None`): per this
        method's design, it's the *client*'s job to fall back to `session/new` on a failed load,
        not this server's. `mcp_servers` is accepted but never acted on, same as
        `new_session`."""
        workspace = self._trust_manager.resolve_workspace(Path(cwd))
        index = read_sessions_index(workspace)
        entry = find_recent_session(index, session_id)
        if entry is None:
            raise acp.RequestError.invalid_params(
                {"sessionId": session_id, "reason": "unknown session"})
        restored = try_restore_session(
            workspace, entry, provider=self._provider, model_registry=self._model_registry,
            process_config=self._process_config)
        if restored is None:
            raise acp.RequestError.invalid_params(
                {"sessionId": session_id, "reason": "session is locked or no longer exists"})
        if self._session is not None:
            logger.debug("session/load replacing live ACP session %s", self._session.id)
            self._session.close()
        self._wire_session_notice_handler(restored)
        self._wire_session_wake_handler(restored)
        restored.fire_session_start_hook("ResumeSession")
        self._session = restored
        # `restored.id` is guaranteed to equal the requested `session_id`.
        self._turn_bridge = TurnBridge(
            restored, self._require_client(), restored.id, self._process_config,
            raise_tool_call_limit_capable=self._client_supports("raiseToolCallLimit"),
            ask_user_questions_capable=self._client_supports("askUserQuestions"))
        logger.debug("session/load restored ACP session %s for cwd=%s", restored.id, cwd)
        entries = build_session_replay(restored, restored.tool_registry, workspace.path)
        await self._require_client().ext_notification(
            "klorb/sessionReplay", {"sessionId": restored.id, "entries": entries})
        return LoadSessionResponse(
            modes=session_mode_state(restored.config.permission_framework),
            field_meta={"klorb": {
                "workspace": {"path": str(workspace.path), "trusted": workspace.trusted},
                "title": restored.name,
            }},
        )

    async def list_sessions(
        self, cursor: str | None = None, cwd: str | None = None, **kwargs: Any,
    ) -> ListSessionsResponse:
        """Return `cwd`'s workspace's saved sessions (`sessions.json`), most-recently-touched
        first. `cursor` is accepted but always ignored -- and `next_cursor` always omitted --
        since the list is small by construction
        (`klorb.workspace.session_store.MAX_RECENT_SESSIONS`), so everything is returned in one
        page. Raises `invalid_params` if `cwd` is omitted: unlike `new_session`/`load_session`,
        ACP allows a client to omit it, but klorb has no process-wide "current workspace" to
        fall back to outside of one already-live session."""
        if cwd is None:
            raise acp.RequestError.invalid_params({"reason": "cwd is required"})
        workspace = self._trust_manager.resolve_workspace(Path(cwd))
        index = read_sessions_index(workspace)
        sessions = [
            SessionInfo(
                cwd=str(workspace.path), session_id=entry.session_id, title=entry.title,
                updated_at=(
                    entry.last_modified_timestamp.isoformat()
                    if entry.last_modified_timestamp else None))
            for entry in index.recent_sessions
        ]
        return ListSessionsResponse(sessions=sessions)

    # -- Protocol surface not implemented at this checkpoint --
    #
    # `acp.Agent` is a `Protocol`; explicitly subclassing one (as `KlorbAcpAgent` does, for a
    # concrete, type-checked implementation) requires overriding every member, so mypy treats
    # each unimplemented one as abstract. None of these are ever dispatched by a compliant
    # client: `initialize()` never advertises auth methods/model selection (`session/set_model`
    # stays unimplemented)/fork/resume -- rejecting explicitly here, rather than an
    # inherited no-op returning `None`, is what a client would see if it tried anyway.

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
        if method == "klorb/setSessionTitle":
            return self._ext_set_session_title(params)
        if method == "klorb/sessionStats":
            return self._ext_session_stats(params)
        if method == "klorb/reloadSkills":
            return self._ext_reload_skills(params)
        if method == "klorb/listSkills":
            return self._ext_list_skills(params)
        if method == "klorb/trustWorkspace":
            return self._ext_trust_workspace(params)
        if method == "klorb/enqueueMessage":
            return self._ext_enqueue_message(params)
        if method == "klorb/subagentTree":
            return self._ext_subagent_tree(params)
        if method == "klorb/subagentTranscript":
            return self._ext_subagent_transcript(params)
        if method == "klorb/subagentCancel":
            return self._ext_subagent_cancel(params)
        if method == "klorb/subagentPrompt":
            return self._ext_subagent_prompt(params)
        raise acp.RequestError.method_not_found(f"_{method}")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        """Unrecognized extension notifications are ignored, per the ACP extensibility rules
        klorb follows."""


def _extract_prompt_content(
    blocks: list[_PromptContentBlock], active_model: Model | None,
    image_pipeline_config: ImagePipelineConfig,
) -> tuple[str, list[MessageFragment]]:
    """Split `blocks` into the concatenated text of every `text` block and one `MessageFragment`
    per `image` block, resizing/transcoding each image for `active_model` via `klorb.images.
    prepare.prepare_image_for_model` using `image_pipeline_config`. Raises a JSON-RPC
    `invalid params` error on the first `image` block if `active_model` is `None` or its
    `capabilities()["vision"]` is falsy, or if `prepare_image_for_model` raises
    `ImageTooLargeError`; audio/resource blocks keep raising `invalid_params` unconditionally.
    """
    texts: list[str] = []
    image_fragments: list[MessageFragment] = []
    for block in blocks:
        if block.type == "text":
            texts.append(block.text)
            continue
        if block.type != "image":
            raise acp.RequestError.invalid_params(
                {"reason": f"content block type {block.type!r} is not supported yet"})
        if active_model is None or not active_model.capabilities().get("vision"):
            raise acp.RequestError.invalid_params(
                {"reason": "the active model does not support image input"})
        try:
            prepared = prepare_image_for_model(base64.b64decode(
                block.data), active_model, image_pipeline_config)
        except ImageTooLargeError as exc:
            raise acp.RequestError.invalid_params({"reason": str(exc)}) from exc
        klorb_meta = (block.field_meta or {}).get("klorb") or {}
        image_fragments.append(MessageFragment(
            type="image_url",
            image_url={"url": f"data:{prepared.mime_type};base64,{prepared.data_b64}"},
            mime_type=prepared.mime_type,
            source_filename=klorb_meta.get("filename"),
            original_width=prepared.original_width,
            original_height=prepared.original_height,
            resized_width=prepared.width,
            resized_height=prepared.height,
        ))
    return "".join(texts), image_fragments
