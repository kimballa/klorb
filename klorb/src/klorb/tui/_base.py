# © Copyright 2026 Aaron Kimball
"""`ReplAppBase`: attribute-only declarations shared by every `ReplApp` mixin.
"""

import asyncio
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from textual import work
from textual.app import App
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static

from klorb.agents.runtime import SubagentHandle, SubagentState
from klorb.message import Message as ChatMessage
from klorb.message import ToolCallRequest
from klorb.process_config import ProcessConfig
from klorb.session import (
    AskUserQuestionsAnswer,
    AskUserQuestionsItemContext,
    EscalatePrivilegesContext,
    EscalatePrivilegesDecision,
    PermissionAskContext,
    PermissionDecision,
    Session,
    ToolCallEvent,
    TurnEventHandlers,
)
from klorb.tui.widgets.tool_call_widgets import (
    RenderedToolCall,
    RunningToolCallStatic,
    ToolCallStatic,
    TurnWaitingStatic,
)
from klorb.tui.workspace_file_index import WorkspaceFileIndex
from klorb.watchdog import LivenessWatchdog
from klorb.workspace import TrustManager, Workspace
from klorb.workspace.session_store import RecentSession


class ReplAppBase(App[None]):
    """Attribute and cross-mixin method declarations for every field/method `ReplApp` and its
    mixins reference on `self` from outside the file that actually defines it, so each mixin
    file type-checks on its own despite referencing state or behavior a different mixin (or
    `ReplApp` itself) sets up. Method stubs here are never called; every one is overridden by
    the mixin that actually owns it once mixed into the concrete `ReplApp`.
    """

    _process_config: ProcessConfig
    _session: Session
    _initial_message: str | None
    _session_log_enabled: bool
    _trust_manager: TrustManager | None
    _config_flag_path: Path | None
    _skip_session_restore: bool
    _quit_on_success: bool
    _final_turn_response: str | None
    _cancel_event: threading.Event | None
    _shell_cancel_event: threading.Event | None
    _last_ctrl_c_at: float
    _last_ctrl_c_kind: Literal["copy", "interrupt", "bare"] | None
    _interrupt_notice_shown: bool
    _interrupt_notice_widget: Static | None
    _watchdog: LivenessWatchdog
    _turn_in_flight: bool
    _interaction_lock: asyncio.Lock
    _release_pending_interaction: Callable[[], None] | None
    _exit_requested: bool
    _last_permission_action: Literal["allow", "deny"]
    _last_permission_scope: Literal["once", "session", "workspace", "homedir"]
    _tool_call_widgets: list[ToolCallStatic]
    _running_tool_call_widgets: dict[str, RunningToolCallStatic]
    _tool_call_detail_shown: bool
    _history_pinned_to_bottom: bool
    _turn_waiting_widget: TurnWaitingStatic | None
    _active_sidebar: str | None
    _queued_message_widgets: list[Static]
    _active_turn_callbacks: TurnEventHandlers | None
    _file_index: WorkspaceFileIndex | None
    _selected_session: Session
    _selected_handle: SubagentHandle | None
    _attention_needed: dict[str, None]
    _subagent_drafts: dict[str, str]
    _blink_phase: bool
    _subagent_history_pinned_to_bottom: bool
    _subagent_history_rendered_count: int
    _subagent_history_rendered_state: SubagentState | None
    _subagent_transcript_notice: Static | None
    _subagent_interrupt_pending: str | None
    _replacing_session: bool

    def _start_file_finder_index(self, workspace: Workspace) -> None: ...

    def _update_status_bar(self) -> None: ...

    def _update_session_name_line(self, text: str) -> None: ...

    def _mount_turn_waiting_widget(self) -> TurnWaitingStatic:
        raise NotImplementedError

    def _clear_turn_waiting_widget(self) -> None: ...

    def _update_permission_badge(self) -> None: ...

    def _update_palette_hint(self) -> None: ...

    def _on_history_scroll_changed(self) -> None: ...

    @work()
    async def _run_startup_workspace_and_initial_message(self) -> None: ...

    def _finish_turn(
        self, history: VerticalScroll, was_pinned: bool, *, agent_turn_succeeded: bool,
        response_text: str | None = None,
    ) -> None: ...

    def _mount_mascot_greeting(self, history: VerticalScroll) -> None: ...

    def _submit_prompt(self, prompt_text: str) -> None: ...

    def _mount_restored_history(self, messages: list[ChatMessage]) -> None: ...

    def _adopt_restored_session(self, restored: Session) -> None: ...

    def list_recent_sessions(self) -> list[RecentSession]:
        raise NotImplementedError

    def load_recent_session(self, entry: RecentSession) -> None: ...

    def show_notice(self, message: str, *, error: bool = False) -> None: ...

    def _wire_session_notice_handler(self, session: Session) -> None: ...

    def _wire_session_wake_handler(self, session: Session) -> None: ...

    def _refresh_header_title(self) -> None: ...

    def _scroll_if_pinned(self, history: VerticalScroll, was_pinned: bool) -> None: ...

    def _begin_exit(self) -> None: ...

    def _ensure_turn_finished(self, own_cancel_event: threading.Event) -> None: ...

    def _resolve_interrupt_notice(self) -> None: ...

    def _mount_response_widget(self, initial_text: str) -> Markdown:
        raise NotImplementedError

    def _update_response_widget(self, widget: Markdown, text: str) -> None: ...

    def _mount_thinking_widget(self, initial_text: str) -> tuple[Static, Static]:
        raise NotImplementedError

    def _update_thinking_widget(self, widget: Static, text: str) -> None: ...

    def _mount_reasoning_details_widget(self, text: str) -> tuple[Static, Static]:
        raise NotImplementedError

    def _update_reasoning_details_widget(self, widget: Static, text: str) -> None: ...

    def _render_tool_call(self, event: ToolCallEvent) -> RenderedToolCall:
        raise NotImplementedError

    def _mount_tool_call_widget(self, rendered: RenderedToolCall) -> tuple[ToolCallStatic, Static]:
        raise NotImplementedError

    def _render_tool_call_summary(self, name: str, args: dict[str, Any]) -> str:
        raise NotImplementedError

    def _mount_running_tool_call_widget(
        self, call_id: str, summary_text: str,
    ) -> RunningToolCallStatic:
        raise NotImplementedError

    def _running_tool_call_anchor(self) -> Static | None: ...

    def _finalize_running_tool_call_widget(
        self, widget: RunningToolCallStatic, rendered: RenderedToolCall,
    ) -> None: ...

    def _on_tool_call_limit_reached(self, message: str) -> bool:
        raise NotImplementedError

    def _on_permission_ask(self, ask_ctx: PermissionAskContext) -> PermissionDecision:
        raise NotImplementedError

    def _on_ask_user_questions(
        self, ask_ctx: AskUserQuestionsItemContext,
    ) -> AskUserQuestionsAnswer:
        raise NotImplementedError

    def _on_escalate_privileges(
        self, escalate_ctx: EscalatePrivilegesContext,
    ) -> EscalatePrivilegesDecision:
        raise NotImplementedError

    def _maybe_refresh_task_sidebar_after_tool_call(self, event: ToolCallEvent) -> None: ...

    @work(thread=True)
    def _refresh_task_sidebar(self) -> None: ...

    def _render_restored_tool_call(
        self, call: ToolCallRequest, response: ChatMessage | None,
    ) -> RenderedToolCall:
        raise NotImplementedError

    def _update_prompt_input_disabled_state(self) -> None: ...

    async def _await_session_selected(self, session_id: str) -> None: ...

    def _start_subagents_panel_timer(self) -> None: ...

    def _on_subagent_history_scroll_changed(self) -> None: ...

    def _note_subagent_interrupt_requested(self, handle: SubagentHandle) -> None: ...
