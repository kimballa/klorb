# © Copyright 2026 Aaron Kimball
"""`ReplApp`: the Textual `App` at the center of the interactive klorb REPL, assembled from
the mixins in `klorb.tui.mixins` -- core lifecycle (this file), key handling/quit/watchdog,
workspace bootstrap/trust, status bar, prompt submission, rendering, and interaction flows.
"""

import asyncio
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Literal

from textual.actions import SkipAction
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.geometry import Offset
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Header, Static

from klorb.agents.policy import compute_root_session_grants
from klorb.agents.runtime import SubagentHandle, SubagentState
from klorb.logging_config import TuiHistoryNotice
from klorb.models.model import Model
from klorb.process_config import ProcessConfig, persist_session_default, persist_theme, user_config_path
from klorb.session import Session, ThinkingEffort, TurnEventHandlers
from klorb.session_statistics import SessionStatistics
from klorb.tui._base import ReplAppBase
from klorb.tui.commands.init_commands import InitCommandProvider
from klorb.tui.commands.model_commands import ModelCommandProvider
from klorb.tui.commands.model_info_commands import ModelInfoCommandProvider
from klorb.tui.commands.session_commands import SessionCommandProvider
from klorb.tui.commands.skill_commands import SkillCommandProvider
from klorb.tui.commands.theme_commands import ThemeCommandProvider
from klorb.tui.commands.thinking_commands import ThinkingCommandProvider
from klorb.tui.commands.trust_commands import TrustWorkspaceCommandProvider
from klorb.tui.constants import (
    HISTORY_ID,
    INTERACTION_PANEL_ID,
    NEW_SESSION_LABEL,
    OUTPUT_TOKENS_ID,
    PALETTE_HINT_ID,
    PERMISSION_BADGE_ID,
    PROMPT_INPUT_ID,
    SESSION_NAME_ID,
    STATUS_BAR_ID,
    SUBAGENT_ATTENTION_STATUS_ID,
    SUBAGENT_HISTORY_ID,
    SUBAGENTS_PANEL_ID,
    TASK_SIDEBAR_ID,
)
from klorb.tui.formatting import format_workspace_path
from klorb.tui.mixins.interactions import InteractionsMixin
from klorb.tui.mixins.key_actions import KeyActionsMixin
from klorb.tui.mixins.prompt_submission import PromptSubmissionMixin
from klorb.tui.mixins.rendering import RenderingMixin
from klorb.tui.mixins.status_bar import StatusBarMixin
from klorb.tui.mixins.subagents_panel import SubagentsPanelMixin
from klorb.tui.mixins.task_sidebar import TaskSidebarMixin
from klorb.tui.mixins.workspace_bootstrap import WorkspaceBootstrapMixin
from klorb.tui.widgets.file_finder import FILE_FINDER_ID, FileFinderPanel
from klorb.tui.widgets.palette import PROMPT_PALETTE_ID, PromptPalette
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.tui.widgets.status_widgets import PaletteHint, PermissionBadge
from klorb.tui.widgets.subagents_panel import SubagentsPanel
from klorb.tui.widgets.task_sidebar import TaskSidebar
from klorb.tui.widgets.tool_call_widgets import RunningToolCallStatic, ToolCallStatic, TurnWaitingStatic
from klorb.tui.workspace_file_index import WorkspaceFileIndex
from klorb.watchdog import LivenessWatchdog
from klorb.workspace import TrustManager, Workspace


class SelectionSafeScreen(Screen[None]):
    """The app's default screen, hardened against a Textual text-selection race.

    Textual starts an app-level text selection on `MouseDown` by hit-testing the coordinate,
    then reading `widget.parent.region` to anchor the selection. During a streaming `Markdown`
    re-render, paragraph widgets are detached and remounted rapidly, so a click can land on a
    `MarkdownParagraph` that the compositor's spatial map still reports at that coordinate even
    though it has already been detached (`parent is None`). Textual then dereferences the
    `None` parent and the whole app crashes with `AttributeError: 'NoneType' object has no
    attribute 'region'`.

    We drop a hit on a detached widget so selection simply doesn't begin on it; the next
    render re-hit-tests cleanly. See
    `docs/adrs/00118-drop-mousedown-on-detached-widget-to-avoid-selection-crash.md`.
    """

    def get_widget_and_offset_at(
        self, x: int, y: int
    ) -> tuple[Widget | None, Offset | None]:
        widget, offset = super().get_widget_and_offset_at(x, y)
        if widget is not None and not isinstance(widget, Screen) and widget.parent is None:
            return None, None
        return widget, offset

    def action_copy_text(self) -> None:
        """Copy the current screen-level (mouse-drag) text selection to the clipboard, same as
        the base `Screen.action_copy_text`, but also records the press for `ReplApp.
        action_interrupt`'s Ctrl+C streak bookkeeping (`ReplApp._note_ctrl_c_copy`) — see that
        method's docstring for why the bookkeeping has to happen here rather than in
        `action_interrupt` itself. Raises `SkipAction`, exactly like the base implementation,
        when nothing is selected, so the binding chain falls through to `ReplApp.action_interrupt`
        unchanged.
        """
        selection = self.get_selected_text()
        if selection is None:
            raise SkipAction()
        self.app.copy_to_clipboard(selection)
        if isinstance(self.app, ReplApp):
            self.app._note_ctrl_c_copy()


class ReplApp(
    KeyActionsMixin,
    WorkspaceBootstrapMixin,
    StatusBarMixin,
    PromptSubmissionMixin,
    RenderingMixin,
    InteractionsMixin,
    TaskSidebarMixin,
    SubagentsPanelMixin,
    ReplAppBase,
):
    """Interactive REPL: a scrolling history of prompts/responses, with a bottom input box."""

    CSS = """
    Screen {
        overflow-y: hidden;
    }

    #history {
        height: 1fr;
    }

    #subagent-history {
        height: 1fr;
        display: none;
    }

    #interaction-panel {
        height: auto;
    }

    #subagent-attention-status {
        height: 1;
        color: $text-muted;
        padding: 0 1;
        display: none;
    }

    #prompt-input, #prompt-input:focus {
        border: none;
        border-top: solid $accent;
        height: auto;
        padding: 0 1;
    }

    #prompt-input.interaction-active {
        height: 1;
        color: $text-muted;
    }

    #session-name {
        height: 1;
        color: $text-muted;
        padding: 0 1;
    }

    #status-row {
        dock: bottom;
        height: 1;
    }

    #status-row Footer {
        dock: none;
        width: 1fr;
        height: 1;
    }

    #status-bar {
        width: auto;
        color: $footer-description-foreground;
        background: $footer-background;
        padding: 0 1;
    }

    #output-tokens {
        width: auto;
        color: $footer-description-foreground;
        background: $footer-background;
        padding: 0 1;
    }

    .prompt {
        color: $accent;
        text-style: bold;
        margin: 1 0 0 0;
    }

    .error {
        color: $error;
        margin: 0 0 1 0;
    }

    .thinking-label {
        color: $text-muted;
        margin: 1 0 0 0;
    }

    .thinking-body {
        color: $text-muted;
        padding: 0 2;
        text-style: italic;
    }

    .reasoning-details-label {
        color: $text-muted;
        margin: 1 0 0 0;
    }

    .reasoning-details-body {
        color: $text-muted;
        padding: 0 2;
        text-style: italic;
    }

    .tool-call-label {
        color: $text-muted;
        margin: 1 0 0 0;
    }

    .tool-call {
        color: $text-muted;
        padding: 0 2;
    }

    .interaction-record-label {
        color: $text-muted;
        margin: 1 0 0 0;
    }

    .interaction-record-body {
        color: $text-muted;
        padding: 0 2;
    }

    .interaction-record-decision {
        color: $text;
        padding: 0 2;
        text-style: bold;
    }

    .response {
        margin: 1 0 0 0;
    }

    .queued-prompt-header {
        color: $text-muted;
        margin: 1 0 0 0;
        text-style: italic;
    }

    .queued-prompt {
        color: $accent;
        text-style: bold italic;
    }

    .interrupted {
        color: $text-muted;
        margin: 1 0 0 0;
    }

    .notice {
        color: $text-muted;
        margin: 1 0 0 0;
    }

    .mascot {
        color: $accent;
        text-align: center;
        margin: 1 0 0 0;
    }
    """

    BINDINGS = [
        ("ctrl+c", "interrupt", "Quit"),
        ("ctrl+q", "quit", "Quit"),
        ("escape", "abort_response", "Abort"),
        ("ctrl+o", "toggle_tool_call_detail", "Detail"),
        ("ctrl+t", "toggle_task_sidebar", "Tasks"),
        ("ctrl+g", "toggle_subagents_panel", "Agents"),
        Binding("shift+tab", "cycle_permission_framework", "Cycle permission", priority=True),
    ]
    COMMANDS = App.COMMANDS | {
        InitCommandProvider, ModelCommandProvider, ModelInfoCommandProvider, SessionCommandProvider,
        SkillCommandProvider, ThemeCommandProvider, ThinkingCommandProvider, TrustWorkspaceCommandProvider,
    }

    def __init__(
        self,
        session: Session | None = None,
        process_config: ProcessConfig | None = None,
        initial_message: str | None = None,
        session_log_enabled: bool = True,
        trust_manager: TrustManager | None = None,
        config_flag_path: Path | None = None,
        skip_session_restore: bool = False,
        quit_on_success: bool = False,
    ) -> None:
        super().__init__()
        self._process_config = process_config or ProcessConfig()
        if session is None:
            new_config = self._process_config.session.model_copy()
            grants = compute_root_session_grants(self._process_config, new_config, new_config.role_name)
            new_config.skill_rules = grants.skill_rules
            session = Session(
                new_config,
                process_config=self._process_config,
                tool_registry=grants.tool_registry,
                effective_subagent_roles=grants.effective_subagent_roles,
            )
        self._session = session
        self._initial_message = initial_message
        self._session_log_enabled = session_log_enabled
        self._trust_manager = trust_manager
        """Set only by a real `klorb.cli.main()` invocation — opts this app into resolving and
        (if needed) interactively bootstrapping workspace trust at startup, and into offering
        the "Trust workspace" palette command (see `docs/specs/projects-and-trust.md`). `None`
        (the default, and every existing test's constructor call) disables all of that: no
        startup prompts, no history announcement, no palette command — so nothing needs a real
        or fake `TrustManager`/`$KLORB_DATA_DIR` just to construct a `ReplApp`."""
        self._config_flag_path = config_flag_path
        """The `--config` path (if any) `klorb.cli.main()`'s initial `load_process_config()`
        call was given, threaded through so `_apply_workspace_config`'s later reload (once a
        workspace's trust/registration state changes) layers it back in identically."""
        self._skip_session_restore = skip_session_restore
        """Set by `klorb --new`: skips `WorkspaceBootstrapMixin._maybe_restore_latest_session`
        at startup even for a trusted workspace with saved sessions on disk, so the app always
        starts from the freshly constructed `Session` instead."""
        self._quit_on_success = quit_on_success
        """Set by `klorb --quit-on-success`: once a model turn finishes with a response and no
        message was queued during it, `PromptSubmissionMixin._finish_turn` closes the session
        and exits the process instead of leaving the REPL open. Latched back to `False` by that
        same method the first time a turn errors, is aborted, or has a message queued during it
        -- see `_finish_turn` and docs/adrs/00177-quit-on-success-latches-off-once-disregarded.md."""
        self._final_turn_response: str | None = None
        """The response text of the turn that triggered a `--quit-on-success` exit, set by
        `PromptSubmissionMixin._finish_turn` right before it closes the session and quits --
        `None` on every other exit path. `klorb.tui.entrypoint.run_repl` prints this to stdout
        once `App.run()` returns and the TUI has actually torn down, so a quit-on-success exit
        still leaves the agent's final answer visible on the terminal."""
        self._file_index: WorkspaceFileIndex | None = None
        """The `@`-mention file finder's workspace index, started (`_start_file_finder_index`)
        only once workspace trust is resolved and only for a real `trust_manager` -- the same
        gate `set_history_store` uses -- so a `ReplApp` built without one (every existing test)
        never spawns a background filesystem watcher against a real directory."""
        self._cancel_event: threading.Event | None = None
        self._shell_cancel_event: threading.Event | None = None
        self._last_ctrl_c_at: float = 0.0
        """`time.monotonic()` of the most recent Ctrl+C press of any kind (copy, interrupt, or
        idle/"bare") — paired with `_last_ctrl_c_kind` below to drive `action_interrupt`'s
        streak escalation to `_force_exit` within `_DOUBLE_INTERRUPT_WINDOW_SECONDS`."""
        self._last_ctrl_c_kind: Literal["copy", "interrupt", "bare"] | None = None
        """What the most recent Ctrl+C press (at `_last_ctrl_c_at`) actually did — `None` before
        the first one. See `action_interrupt`, `_note_ctrl_c_copy`, and docs/specs/interrupt-and-
        liveness-watchdog.md's "Ctrl+C semantics" section."""
        self._interrupt_notice_shown: bool = False
        """Whether the `_INTERRUPTING_MESSAGE` notice has already been shown for the current
        turn (so repeated Escape/Ctrl+C don't stack duplicate notices) — reset in `_finish_turn`."""
        self._interrupt_notice_widget: Static | None = None
        """The `_INTERRUPTING_MESSAGE` notice `Static` mounted by `_note_interrupt_requested`,
        kept so `_finish_turn` can rewrite it to `_INTERRUPTED_MESSAGE` once the interrupt has
        actually taken hold (the turn has wound down) — reset in `_finish_turn`."""
        self._watchdog: LivenessWatchdog = LivenessWatchdog(
            self._process_config.watchdog_timeout_seconds, self._force_exit)
        """Liveness watchdog force-exiting the process if the main-thread event loop stops
        snoozing it (`_snooze_watchdog`) for `watchdog.timeout` seconds — the escape from a wedged
        event loop. Started/snoozed in `on_mount`, disarmed in `on_unmount`. See
        docs/specs/interrupt-and-liveness-watchdog.md."""
        self._turn_in_flight: bool = False
        """Authoritative "a model turn or shell command is currently running" guard, checked and
        set synchronously in `_submit_prompt`/`_submit_shell_command` and cleared in `_finish_turn`.
        The prompt input's `disabled` flag is only a visual affordance and can NOT be relied on to
        serialize turns: pressing Enter posts a `Submitted` message synchronously (see
        `PromptInput._on_key`/`_record_and_submit`), but `disabled` is set only later, when the app
        *handles* that message. Two submits queued inside that window (a double Enter, a bracketed
        paste containing a newline, ...) therefore both reach `on_prompt_input_submitted` before
        either disables the box, and without this flag both would dispatch — two concurrent
        `_send_prompt` workers streaming into their own `Markdown` widgets at once, which is the
        stacked-panels / duplicated-turn bug (and the flood of `_GatheringFuture` `CancelledError`s
        from Textual's `Markdown.await_update` lock when the app is torn down mid-stream). Being a
        plain bool touched only from the event-loop thread, its check-and-set is atomic against
        other message handlers."""
        self._interaction_lock = asyncio.Lock()
        """Serializes the mount/await/dismiss lifecycle of every interaction panel (permission
        ask, ask-user-questions, escalate-privileges) so at most one is ever mounted into
        `#interaction-panel` at a time — see `_confirm_permission_ask`/`_confirm_ask_user_questions`/
        `_confirm_escalate_privileges`. A single turn's tool calls are already serial, and
        `_turn_in_flight` keeps a second turn from starting while one is running (so no second
        turn's tool calls can drive a confirmation concurrently); this lock is a further structural
        guarantee that even so a second panel queues behind the first rather than stacking, no
        matter what future path drives a confirmation."""
        self._release_pending_interaction: Callable[[], None] | None = None
        """While an interaction panel (permission ask, ask-user-questions, escalate-privileges) is
        mounted and awaiting the user, this resolves that panel's pending decision future with a
        safe default (deny / cancelled), releasing the worker thread parked inside
        `App.call_from_thread` waiting on it and freeing `_interaction_lock`. Each `_confirm_*`
        method sets it right before it awaits and clears it in a `finally`; `None` whenever no
        interaction is pending. Teardown/abort paths (`_signal_turn_cancellation`,
        `_release_workers_for_exit`) call it so quitting or interrupting can never strand a worker
        thread blocked on a decision that will never come — the deadlock behind both the
        "model hangs" and "can't quit" symptoms (see
        docs/adrs/00120-unblock-worker-thread-before-teardown-so-quit-cannot-hang.md)."""
        self._exit_requested: bool = False
        """Set by `_begin_exit` when a quit is requested while a turn or shell command is still in
        flight: rather than `self.exit()` immediately (which would stop the event loop while the
        worker thread is still parked in `App.call_from_thread`, so its `future.result()` never
        returns and its non-daemon executor thread blocks process shutdown), the exit is deferred
        until the signalled worker unwinds and reaches `_finish_turn`, which performs the real
        `self.exit()` once nothing is running on the loop's behalf anymore."""
        self._last_permission_action: Literal["allow", "deny"] = "allow"
        self._last_permission_scope: Literal["once", "session", "workspace", "homedir"] = "once"
        """`PermissionAskPanel`'s grid cursor position after the most recent prompt this app
        showed, seeded into the next one's `initial_action`/`initial_scope` (see
        `_confirm_permission_ask`) so answering several asks in a row for one compound tool call
        (see `klorb.permissions.table.MultiPermissionAskRequired`) doesn't require re-navigating
        the grid to the same spot each time. Starts at the grid's default top-left cell; updated
        after every dismissal, including "Other..." (which reports back as its
        `action="deny"`/`scope="once"` regardless of what was selected before switching to free
        text, since there's no meaningful grid cell an `Input` submission corresponds to)."""
        self._tool_call_widgets: list[ToolCallStatic] = []
        self._running_tool_call_widgets: dict[str, RunningToolCallStatic] = {}
        self._turn_waiting_widget: TurnWaitingStatic | None = None
        """The `TurnWaitingStatic` mounted by `_submit_prompt` for the in-flight turn, if it
        hasn't been cleared yet (`_clear_turn_waiting_widget`) -- `None` before the first turn
        and in between turns."""
        self._tool_call_detail_shown: bool = False
        self._history_pinned_to_bottom: bool = True
        self._active_sidebar: str | None = self._process_config.sidebar
        """Which sidebar panel is currently visible -- `None`, `"tasks"`, or `"agents"`.
        See `TaskSidebarMixin.action_toggle_task_sidebar` and
        `SubagentsPanelMixin.action_toggle_subagents_panel`."""
        self._selected_session: Session = self._session
        """Whichever session's transcript `#history`/`#subagent-history` currently displays, and
        whose asks `InteractionsMixin._await_session_selected` lets through immediately -- the
        root session by default. See `SubagentsPanelMixin._select_session`."""
        self._selected_handle: SubagentHandle | None = None
        """The `SubagentHandle` `_selected_session` was selected via, or `None` when the root
        session is selected (nothing created it, so there is no handle) -- lets `KeyActionsMixin`
        route Escape/Ctrl+C to the selected subagent's own `cancel_event` instead of the root
        session's."""
        self._attention_needed: dict[str, None] = {}
        """Session ids with an ask waiting for the user to select them (see `InteractionsMixin.
        _await_session_selected`), in the order they started waiting -- a `dict` used as an
        insertion-ordered set. Drives the panel's blinking `(!)` marker and the status-line
        fallback (`SubagentsPanelMixin._update_subagent_attention_status_line`)."""
        self._subagent_drafts: dict[str, str] = {}
        """Prompt-input draft text saved per session id, restored on reselecting a session that
        was blurred away from with unsubmitted text still in the box -- see
        `SubagentsPanelMixin._select_session`."""
        self._blink_phase: bool = False
        """Flipped every `_tick_subagents_panel` tick; whether the `(!)` attention marker is
        currently lit."""
        self._subagent_history_pinned_to_bottom: bool = True
        """Whether `#subagent-history` was scrolled to its end the last time its `scroll_y`
        changed -- the `#subagent-history` analog of `_history_pinned_to_bottom`, kept in sync by
        `SubagentsPanelMixin._on_subagent_history_scroll_changed`."""
        self._subagent_history_rendered_count: int = 0
        """How many of the selected subagent's `Session.messages` `#subagent-history` currently
        shows -- lets `SubagentsPanelMixin._append_new_subagent_messages` mount only the delta on
        each tick instead of rebuilding the whole view. Reset whenever selection changes
        (`_select_session`)."""
        self._subagent_history_rendered_state: SubagentState | None = None
        """The selected subagent's `SubagentHandle.state` as of the last `#subagent-history`
        render -- lets `_append_new_subagent_messages` notice a running/finished transition (and
        re-render the trailing status notice) even on a tick with no new messages."""
        self._subagent_transcript_notice: Static | None = None
        """The trailing status notice currently mounted in `#subagent-history`, if any -- tracked
        so the next render can remove it before mounting anything new, keeping it last. `None`
        before any subagent has ever been selected."""
        self._subagent_interrupt_pending: str | None = None
        """The id of a subagent whose turn was just aborted (Escape/Ctrl+C while it was
        selected -- `KeyActionsMixin._interrupt_running_activity`) but whose handle hasn't
        finished yet, or `None`. Keeps `_mount_subagent_status_notice` showing "Sending
        interrupt…" across every tick until the abort actually lands. See
        `SubagentsPanelMixin._note_subagent_interrupt_requested`."""
        self._queued_message_widgets: list[Static] = []
        """History widgets for user messages queued during the agent turn, tracked so
        `_finish_turn` can transition them from italics to regular once delivered."""
        self._active_turn_callbacks: TurnEventHandlers | None = None
        """The `TurnEventHandlers` built by `_send_prompt` for the turn currently in flight, or
        `None` between turns. `Session.drain_queued_messages()`'s `on_send_queued_message` hook
        needs a live callbacks object to fire against; `Session._current_turn_handlers` isn't
        usable for `_finish_turn`'s end-of-turn drain because `_dispatch_turn` already clears it
        before `send_turn()` returns to `_send_prompt`, i.e. before `_finish_turn` ever runs. See
        `_send_prompt`/`_finish_turn`."""
        if self._process_config.theme is not None and self._process_config.theme in self.available_themes:
            self.theme = self._process_config.theme
        self.title = "klorb"
        self.sub_title = self._session.config.model

    def compose(self) -> ComposeResult:
        yield Header()
        yield TaskSidebar(id=TASK_SIDEBAR_ID)
        yield SubagentsPanel(id=SUBAGENTS_PANEL_ID)
        yield VerticalScroll(id=HISTORY_ID)
        yield VerticalScroll(id=SUBAGENT_HISTORY_ID)
        yield Vertical(id=INTERACTION_PANEL_ID)
        yield FileFinderPanel(id=FILE_FINDER_ID)
        yield PromptPalette(id=PROMPT_PALETTE_ID)
        yield PromptInput(placeholder="Send a message...", id=PROMPT_INPUT_ID)
        yield Static(NEW_SESSION_LABEL, id=SESSION_NAME_ID)
        yield Static(id=SUBAGENT_ATTENTION_STATUS_ID)
        with Horizontal(id="status-row"):
            yield PaletteHint(id=PALETTE_HINT_ID)
            yield Footer(show_command_palette=False)
            yield PermissionBadge(id=PERMISSION_BADGE_ID)
            yield Static(id=STATUS_BAR_ID)
            yield Static(id=OUTPUT_TOKENS_ID)

    def get_default_screen(self) -> Screen:
        """Use `SelectionSafeScreen` so a click during a streaming `Markdown` re-render can't
        crash the app via Textual's text-selection hit-test (see the class docstring)."""
        return SelectionSafeScreen(id="_default")

    def get_current_model_name(self) -> str:
        """Return the identifier of the currently active model — see `ModelCommandProvider`."""
        return self._session.config.model

    def get_active_model(self) -> Model | None:
        """Return the currently active `Model`, or `None` if `config.model` isn't a
        registered model — see `klorb.tui.commands.model_info_commands.ModelInfoCommandProvider`."""
        return self._session.active_model()

    def available_model_names(self) -> list[str]:
        """Return every model name discovered by the session's `ModelRegistry`, sorted for a
        stable display order — see `ModelCommandProvider`."""
        return sorted(model.name() for model in self._session.model_registry.models())

    def workspace_files(self) -> list[str]:
        """Return the `@`-mention file finder's current workspace-relative file list, or `[]`
        if `_file_index` was never started (no `trust_manager`, e.g. every existing test) — see
        `_start_file_finder_index`."""
        return self._file_index.files if self._file_index is not None else []

    def _start_file_finder_index(self, workspace: Workspace) -> None:
        """Start the `@`-mention file finder's background `WorkspaceFileIndex` for `workspace`,
        refreshing the popup (if currently shown) whenever the index's file list changes.
        Called only from `_resolve_workspace_trust`, gated on `trust_manager` the same way
        `PromptInput.set_history_store` is, so a `ReplApp` built without one never spawns a
        background filesystem watcher against a real directory.
        """
        def on_changed() -> None:
            try:
                self.call_from_thread(self._refresh_file_finder_after_index_change)
            except RuntimeError:
                # The app's event loop has already stopped (e.g. mid-shutdown, racing
                # `on_unmount`'s `_file_index.close()`) -- nothing is left to refresh.
                pass

        self._file_index = WorkspaceFileIndex(workspace.path, on_changed=on_changed)
        self._file_index.start()

    def _refresh_file_finder_after_index_change(self) -> None:
        """Re-run the file finder's current query against the just-updated workspace file list,
        so a create/delete elsewhere is reflected in an already-open popup instead of it going
        stale until the next keystroke."""
        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.call_later(prompt_input.refresh_finder)

    def get_session_statistics(self) -> SessionStatistics:
        """Return the active session's running statistics — see
        `klorb.tui.commands.session_commands.SessionCommandProvider`."""
        return self._session.statistics

    def select_model(self, name: str) -> None:
        """Make `name` the active model used for subsequent prompts, the default model for any
        future session started in this process, and — persisted to the per-user config file
        (`sessionDefaults.model`) — the default for every future klorb process too.
        """
        self._session.config.model = name
        self._process_config.session.model = name
        persist_session_default(user_config_path(), "model", name)
        self.sub_title = name
        self._update_status_bar()
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(f"Model set to {name}.", classes="notice"))

    def show_notice(self, message: str, *, error: bool = False) -> None:
        """Append `message` to the history scroll as a `.notice` (or `.error` if `error`)
        item. Exposed for callers outside `ReplApp` (e.g. `InitCommandProvider`) that report a
        one-off status/result but shouldn't otherwise depend on history-widget internals like
        `HISTORY_ID`/`VerticalScroll`. Constructed with `markup=False`: `message` can be
        arbitrary (e.g. an exception's text), which must render verbatim rather than be parsed
        as Textual console markup.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(message, classes="error" if error else "notice", markup=False))
        history.scroll_end(animate=False)

    def on_tui_history_notice(self, message: TuiHistoryNotice) -> None:
        """Handles a `TuiHistoryLogHandler`-posted `WARNING`+ log record by mounting it into the
        conversation history via `show_notice()` -- see `klorb.logging_config`."""
        self.show_notice(message.text, error=message.error)

    def reload_skills(self) -> None:
        """Rebuild the active session's skill catalog (`klorb.tools.skill.catalog`) from a fresh
        disk scan against its workspace, and report the resulting skill count in the history
        scroll -- see `klorb.tui.commands.skill_commands.SkillCommandProvider`."""
        catalogs = self._session.reload_skills()
        count = len(catalogs.canonical)
        self.show_notice(f"Reloaded skill catalog: {count} skill{'s' if count != 1 else ''} found.")

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        """Every default system command except "Theme" — `ThemeCommandProvider` supersedes it
        with a command that shows the current theme name in its own label and opens
        `ThemeSelectionScreen`'s `(*)`-marked picker instead of Textual's own unmarked one.
        """
        for command in super().get_system_commands(screen):
            if command.title != "Theme":
                yield command

    def get_current_theme_name(self) -> str:
        """Return the name of the currently-active Textual theme — see `ThemeCommandProvider`."""
        return self.theme

    def available_theme_names(self) -> list[str]:
        """Return every registered Textual theme name, sorted for a stable display order in
        `ThemeSelectionScreen` — see `ThemeCommandProvider`.
        """
        return sorted(self.available_themes)

    def select_theme(self, name: str) -> None:
        """Make `name` the active Textual theme, persist it to the per-user config file
        (`persist_theme`) so it's restored on the next klorb session, and announce the change
        in the history scroll.
        """
        self.theme = name
        self._process_config.theme = name
        persist_theme(name)
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(f"Changed current theme to `{name}`.", classes="notice"))

    def format_title(self, title: str, sub_title: str) -> Content:
        """Compose the `Header`'s displayed title from the current workspace path (shortened
        per `format_workspace_path` if it's too long to comfortably fit) plus an `"(Untrusted)"`
        marker when the workspace isn't trusted, followed by the *selected* session's
        (`_selected_session` -- the root session by default, or whichever subagent is picked in
        the subagents panel) own model and, if it has thinking enabled, its effort level in
        parentheses — e.g. `".../path/to/somewhere (Untrusted) - gpt-4o (High)"`. Overrides
        `App.format_title`, which otherwise just joins `title`/`sub_title` with an em dash;
        `title` is ignored here since a bare "klorb" app name isn't useful once every session is
        tied to a specific workspace directory. `sub_title` (the reactive `App.sub_title`, kept
        in sync with the *root* session's own model by `select_model`) is likewise not read for
        its value here -- only its *changes* matter, since those are what make `Header` re-invoke
        this method at all; `SubagentsPanelMixin._select_session` covers the case where the
        model that should now be displayed changes without `sub_title` itself changing, via
        `_refresh_header_title()`.
        """
        workspace = self._session.config.workspace
        workspace_display = format_workspace_path(workspace.path)
        if not workspace.trusted:
            workspace_display += " (Untrusted)"
        selected_config = self._selected_session.config
        model_display = selected_config.model
        if selected_config.thinking_enabled:
            model_display += f" ({selected_config.thinking_effort.title()})"
        return Content.assemble(
            Content(workspace_display),
            (" - ", "dim"),
            Content(model_display).stylize("dim"),
        )

    def _refresh_header_title(self) -> None:
        """Force the `Header` to redraw via `format_title()`, for state changes (workspace
        trust, thinking effort/enabled) that it depends on but that `Header` doesn't itself
        watch — unlike `self.title`/`self.sub_title`, whose own changes it watches directly.
        `mutate_reactive` re-runs those watchers with `self.sub_title`'s value unchanged, which
        is enough to make `Header` re-invoke `format_title()`.
        """
        self.mutate_reactive(ReplApp.sub_title)

    def get_thinking_effort(self) -> ThinkingEffort:
        """Return the reasoning effort level currently configured for subsequent prompts."""
        return self._session.config.thinking_effort

    def get_thinking_enabled(self) -> bool:
        """Return whether extended-thinking requests are currently enabled."""
        return self._session.config.thinking_enabled

    def set_thinking_enabled(self, enabled: bool) -> None:
        """Enable or disable extended-thinking requests for subsequent prompts, the default for
        any future session started in this process, and — persisted to the per-user config
        file (`sessionDefaults.thinking.enabled`) — the default for every future klorb process
        too.
        """
        self._session.config.thinking_enabled = enabled
        self._process_config.session.thinking_enabled = enabled
        persist_session_default(user_config_path(), "thinking.enabled", enabled)
        self._refresh_header_title()
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(f"Thinking {'enabled' if enabled else 'disabled'}.", classes="notice"))

    def set_thinking_effort(self, effort: ThinkingEffort) -> None:
        """Set the reasoning effort level used for subsequent prompts, when thinking is
        enabled and the active model supports it. Also becomes the default effort level for
        any future session started in this process, and — persisted to the per-user config
        file (`sessionDefaults.thinking.effort`) — the default for every future klorb process
        too.
        """
        self._session.config.thinking_effort = effort
        self._process_config.session.thinking_effort = effort
        persist_session_default(user_config_path(), "thinking.effort", effort)
        self._refresh_header_title()
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(f"Thinking effort set to {effort}.", classes="notice"))
