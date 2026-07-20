# © Copyright 2026 Aaron Kimball
"""`ReplAppBase`: attribute-only declarations shared by every `ReplApp` mixin.

`ReplApp` itself (`klorb.tui.app`) is composed from several mixins, each holding one
cohesive slice of its ~150 methods, verbatim -- `self.foo(...)` resolves at runtime via MRO
regardless of which mixin `foo` physically lives in, so no call site needs to change. But
this repo's `make typecheck` runs mypy with `--disallow-untyped-calls` and
`--disallow-untyped-globals`, so a mixin method referencing `self._some_attr` (set by
`ReplApp.__init__`, which lives in a different file) needs `_some_attr` visible on `self`'s
declared type from that mixin's own point of view -- mypy doesn't know that whichever
concrete class eventually mixes this one in will also mix in the one that sets it.
`ReplAppBase` is that shared point of view: every mixin declares
`class FooMixin(ReplAppBase):`, and `ReplApp(FooMixin, ..., ReplAppBase)` is the only class
with a real `__init__` body. This class carries no behavior of its own.
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

from klorb.message import Message as ChatMessage
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
)
from klorb.tui.widgets.tool_call_widgets import (
    GettingReadyStatic,
    RenderedToolCall,
    RunningToolCallStatic,
    ToolCallStatic,
    TurnWaitingStatic,
)
from klorb.watchdog import LivenessWatchdog
from klorb.workspace import TrustManager


class ReplAppBase(App[None]):
    """Attribute and cross-mixin method declarations for every field/method `ReplApp` and its
    mixins reference on `self` from outside the file that actually defines it, so each mixin
    file type-checks on its own despite referencing state or behavior a different mixin (or
    `ReplApp` itself) sets up. See the module docstring for why this class exists. Method
    stubs here are never called -- every one is overridden by the mixin that actually owns it
    once mixed into the concrete `ReplApp`.
    """

    _process_config: ProcessConfig
    _session: Session
    _initial_message: str | None
    _session_log_enabled: bool
    _trust_manager: TrustManager | None
    _config_flag_path: Path | None
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
    _session_naming_pending: bool
    _turn_waiting_widget: TurnWaitingStatic | None

    def _update_status_bar(self) -> None: ...

    def _update_session_name_line(self, text: str) -> None: ...

    def _mount_getting_ready_widget(self) -> GettingReadyStatic:
        raise NotImplementedError

    def _mount_turn_waiting_widget(self) -> TurnWaitingStatic:
        raise NotImplementedError

    def _clear_turn_waiting_widget(self) -> None: ...

    def _update_permission_badge(self) -> None: ...

    def _update_palette_hint(self) -> None: ...

    def _on_history_scroll_changed(self) -> None: ...

    @work()
    async def _run_startup_workspace_and_initial_message(self) -> None: ...

    def _finish_turn(self, history: VerticalScroll, was_pinned: bool) -> None: ...

    def _submit_prompt(self, prompt_text: str) -> None: ...

    def _mount_restored_history(self, messages: list[ChatMessage]) -> None: ...

    def show_notice(self, message: str, *, error: bool = False) -> None: ...

    def _refresh_header_title(self) -> None: ...

    def _scroll_if_pinned(self, history: VerticalScroll, was_pinned: bool) -> None: ...

    def _begin_exit(self) -> None: ...

    def _ensure_turn_finished(self) -> None: ...

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
