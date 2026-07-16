# © Copyright 2026 Aaron Kimball
"""A Textual-based interactive REPL: a scrolling prompt/response history with an input
box pinned to the bottom of the screen.
"""

import asyncio
import json
import logging
import sys
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal, TypeVar

from textual import work
from textual.actions import SkipAction
from textual.app import App, ComposeResult, SystemCommand
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.geometry import Offset
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Header, Markdown, Static, TextArea

from klorb.api_provider import ResponseAborted
from klorb.logging_config import CrashLogTee, configure_logging, crash_log_path, session_log_path
from klorb.message import Message as ChatMessage
from klorb.message import ToolCallRequest
from klorb.models.model import Model
from klorb.permissions.command_grant import compute_command_grant_patterns
from klorb.permissions.grant import compute_grant_paths
from klorb.permissions.risk_classifier import record_decision_history, resolve_item_risk_assessment
from klorb.process_config import (
    ProcessConfig,
    apply_cli_flags_to_session,
    load_process_config,
    persist_session_default,
    persist_theme,
    user_config_path,
)
from klorb.session import (
    AskUserQuestionsAnswer,
    AskUserQuestionsItemContext,
    EscalatePrivilegesContext,
    EscalatePrivilegesDecision,
    PermissionAskContext,
    PermissionDecision,
    Session,
    ThinkingEffort,
    ToolCallEvent,
    ToolCallStartedEvent,
    TurnEventHandlers,
)
from klorb.session_statistics import SessionStatistics
from klorb.tools.registry import NoSuchToolException, ToolRegistry
from klorb.tools.tool import (
    default_invalid_tool_call_detail,
    default_invalid_tool_call_summary,
    default_tool_call_detail,
    default_tool_call_summary,
)
from klorb.tui._base import ReplAppBase
from klorb.tui.commands.init_commands import InitCommandProvider
from klorb.tui.commands.model_commands import ModelCommandProvider
from klorb.tui.commands.model_info_commands import ModelInfoCommandProvider
from klorb.tui.commands.session_commands import SessionCommandProvider
from klorb.tui.commands.theme_commands import ThemeCommandProvider
from klorb.tui.commands.thinking_commands import ThinkingCommandProvider
from klorb.tui.commands.trust_commands import TrustWorkspaceCommandProvider
from klorb.tui.constants import (
    HISTORY_ID,
    INTERACTION_PANEL_ID,
    OUTPUT_TOKENS_ID,
    PALETTE_HINT_ID,
    PERMISSION_BADGE_ID,
    PERMISSION_FRAMEWORK_CYCLE,
    PROMPT_INPUT_ID,
    STATUS_BAR_ID,
)
from klorb.tui.formatting import (
    _format_workspace_path,
    _pinned_to_bottom,
    _summarize_reasoning_details,
    format_token_count,
)
from klorb.tui.mixins.key_actions import KeyActionsMixin
from klorb.tui.mixins.workspace_bootstrap import WorkspaceBootstrapMixin
from klorb.tui.panels.ask_user_questions_panel import AskUserQuestionsPanel, format_ask_user_questions_answer
from klorb.tui.panels.escalate_privileges_panel import (
    EscalatePrivilegesPanel,
    format_escalate_privileges_decision,
)
from klorb.tui.panels.permission_ask_panel import (
    PermissionAskPanel,
    format_ask_context_body,
    format_permission_decision,
)
from klorb.tui.shell import ShellCommandCancelled, ShellCommandTimedOut, UserShellCommand
from klorb.tui.widgets.palette import PALETTE_PREFIX, PROMPT_PALETTE_ID, PromptPalette
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.tui.widgets.status_widgets import PaletteHint, PermissionBadge
from klorb.tui.widgets.tool_call_widgets import RunningToolCallStatic, ToolCallLimitScreen, ToolCallStatic
from klorb.watchdog import LivenessWatchdog
from klorb.workspace import TrustManager
from klorb.workspace.last_session import last_session_path, write_last_session

logger = logging.getLogger(__name__)


THINKING_LABEL = "<Thinking>"
REASONING_DETAILS_LABEL = "<Reasoning>"
TOOL_USE_LABEL = "<Tool use>"
INTERACTION_RECORD_LABEL = "<Approval>"

_COMMAND_PREVIEW_WIDTH_PADDING = 4
"""Horizontal space `PermissionAskPanel`'s own `padding: 1 2` (2 columns each side) consumes
around its command preview — subtracted from the app's terminal width to estimate the preview's
actual rendered width for `PermissionAskPanel`'s `preview_wrap_width` (see
`ReplApp._confirm_permission_ask` and `klorb.tui.panels.permission_ask_panel._command_preview`)."""
_MIN_COMMAND_PREVIEW_WRAP_WIDTH = 20
"""Floor for the wrap-width estimate above, so a very narrow terminal still gets a usable
(if aggressively wrapped) preview rather than a degenerate near-zero width."""

_InteractionResult = TypeVar("_InteractionResult")
"""The decision/answer type an interaction panel resolves its future with — see
`ReplApp._register_interaction_future`."""





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
    `docs/adrs/drop-mousedown-on-detached-widget-to-avoid-selection-crash.md`.
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


class ReplApp(KeyActionsMixin, WorkspaceBootstrapMixin, ReplAppBase):
    """Interactive REPL: a scrolling history of prompts/responses, with a bottom input box."""

    CSS = """
    Screen {
        overflow-y: hidden;
    }

    #history {
        height: 1fr;
    }

    #interaction-panel {
        height: auto;
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
    ]
    COMMANDS = App.COMMANDS | {
        InitCommandProvider, ModelCommandProvider, ModelInfoCommandProvider, SessionCommandProvider,
        ThemeCommandProvider, ThinkingCommandProvider, TrustWorkspaceCommandProvider,
    }

    def __init__(
        self,
        session: Session | None = None,
        process_config: ProcessConfig | None = None,
        initial_message: str | None = None,
        session_log_enabled: bool = True,
        trust_manager: TrustManager | None = None,
        config_flag_path: Path | None = None,
    ) -> None:
        super().__init__()
        self._process_config = process_config or ProcessConfig()
        if session is None:
            new_config = self._process_config.session.model_copy()
            session = Session(
                new_config,
                process_config=self._process_config,
                tool_registry=ToolRegistry(self._process_config, new_config),
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
        docs/adrs/unblock-worker-thread-before-teardown-so-quit-cannot-hang.md)."""
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
        self._tool_call_detail_shown: bool = False
        self._history_pinned_to_bottom: bool = True
        if self._process_config.theme is not None and self._process_config.theme in self.available_themes:
            self.theme = self._process_config.theme
        self.title = "klorb"
        self.sub_title = self._session.config.model

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id=HISTORY_ID)
        yield Vertical(id=INTERACTION_PANEL_ID)
        yield PromptPalette(id=PROMPT_PALETTE_ID)
        yield PromptInput(placeholder="Send a message...", id=PROMPT_INPUT_ID)
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
        per `_format_workspace_path` if it's too long to comfortably fit) plus an `"(Untrusted)"`
        marker when the workspace isn't trusted, followed by `sub_title` (the active model,
        kept in sync with it by `select_model`) and, if thinking is enabled, its effort level in
        parentheses — e.g. `".../path/to/somewhere (Untrusted) - gpt-4o (High)"`. Overrides
        `App.format_title`, which otherwise just joins `title`/`sub_title` with an em dash;
        `title` is ignored here since a bare "klorb" app name isn't useful once every session is
        tied to a specific workspace directory.
        """
        workspace = self._session.config.workspace
        workspace_display = _format_workspace_path(workspace.path)
        if not workspace.trusted:
            workspace_display += " (Untrusted)"
        model_display = sub_title
        if self.get_thinking_enabled():
            model_display += f" ({self.get_thinking_effort().title()})"
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

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Keep the `> palette` hint in sync with the prompt input's content, including edits
        that don't go through a key event (e.g. `PromptInput._recall_history`'s `self.text =
        ...` assignments).
        """
        self._update_palette_hint()

    def _update_palette_hint(self) -> None:
        """Show the `PaletteHint` only while the box is empty or holds just the leading `>`
        (i.e. before any real query narrows it) — the statusbar analog of the `Footer`'s own
        `^p` command-palette hint (suppressed via `Footer(show_command_palette=False)` in
        `compose()`, since palette-from-prompt is now the primary way to reach the command
        palette — see `docs/specs/command-palette-from-prompt.md`).
        """
        hint = self.query_one(f"#{PALETTE_HINT_ID}", PaletteHint)
        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        text = prompt_input.text
        if text in ("", PALETTE_PREFIX):
            hint.show_hint()
        else:
            hint.hide_hint()

    def _on_history_scroll_changed(self) -> None:
        """Keep `_history_pinned_to_bottom` in sync with the history viewport's actual scroll
        position, recomputed only when Textual itself changes `scroll_y` — via our own
        `scroll_end()`/`scroll_home()` calls landing, or genuine user scrolling — rather than by
        re-reading `is_vertical_scroll_end` synchronously wherever a streaming update happens.

        Mounting a widget doesn't immediately update `max_scroll_y` (it's only remeasured on the
        next layout refresh, which is exactly why `scroll_end()`/`scroll_home()` themselves defer
        via `call_after_refresh` by default), so eagerly re-checking `is_vertical_scroll_end`
        right after scheduling one streaming update — before an earlier update's own deferred
        scroll has actually landed — could misread a still-pinned viewport as scrolled away.
        Reacting only to `scroll_y` actually changing sidesteps that: by the time this fires,
        `max_scroll_y` is necessarily consistent with the `scroll_y` that triggered it.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        self._history_pinned_to_bottom = _pinned_to_bottom(history)

    def _update_status_bar(self) -> None:
        """Refresh both footer token tallies: context usage vs. the model's context window, and
        total model-output tokens.
        """
        status_bar = self.query_one(f"#{STATUS_BAR_ID}", Static)
        used = format_token_count(self._session.total_tokens_used())
        limit = self._session.max_context_window()
        if limit is None:
            status_bar.update(f"\u2191 {used}")
        else:
            status_bar.update(f"\u2191 {used} / {format_token_count(limit)}")

        output_tokens = self.query_one(f"#{OUTPUT_TOKENS_ID}", Static)
        output_tokens.update(f"\u2193 {format_token_count(self._session.total_output_tokens_used())}")

    def _update_permission_badge(self) -> None:
        """Set the permission badge to show `Session.config.permission_framework`
        (`[ask]`/`[auto]`/`[deny]`), so the user always knows whether tool-permission asks
        are being shown interactively, auto-approved, or auto-denied. Called once from
        `on_mount()`, with no flash -- `_cycle_permission_framework()` is what changes the
        value (and flashes the badge) after startup.
        """
        badge = self.query_one(f"#{PERMISSION_BADGE_ID}", PermissionBadge)
        badge.set_value(self._session.config.permission_framework)

    def _cycle_permission_framework(self) -> None:
        """Advance `Session.config.permission_framework` to the next value in
        `PERMISSION_FRAMEWORK_CYCLE` (wrapping around), and flash the badge to draw the eye
        to the change. Bound to Shift+Tab via `PromptInput.CyclePermissionFramework` (see
        `on_prompt_input_cycle_permission_framework`) since Shift+Tab isn't otherwise
        meaningful in this single-input-focus app. Goes through `Session.set_permission_
        framework()`, not a direct assignment, so the model is told about the change via a
        system-harness interjection prepended to the next turn — see
        docs/specs/permissions.md's "Permission framework change interjection" section.
        """
        current = self._session.config.permission_framework
        next_index = (PERMISSION_FRAMEWORK_CYCLE.index(current) + 1) % len(PERMISSION_FRAMEWORK_CYCLE)
        next_value = PERMISSION_FRAMEWORK_CYCLE[next_index]
        self._session.set_permission_framework(next_value)
        badge = self.query_one(f"#{PERMISSION_BADGE_ID}", PermissionBadge)
        badge.flash_to(next_value)

    def on_prompt_input_cycle_permission_framework(
        self, event: "PromptInput.CyclePermissionFramework",
    ) -> None:
        """`PromptInput` posts this on Shift+Tab; hand off to `_cycle_permission_framework()`."""
        self._cycle_permission_framework()

    def on_prompt_input_submitted(self, event: PromptInput.Submitted) -> None:
        """Echo the submitted prompt into the history and dispatch it to the model, or
        handle `:q`/`/quit`/`/exit` or a `!`-prefixed shell command synchronously.

        `PromptInput._execute_palette_hit` handles a palette selection (e.g. `>Clear
        session`) entirely on its own — invoking the hit's command directly rather than
        posting `Submitted` — so a `>`-prefixed `prompt_text` reaching here is always one
        that ruled out every palette option, dispatched as an ordinary prompt like any other
        (see `docs/specs/command-palette-from-prompt.md`).
        """
        prompt_text = event.value.strip()
        if not prompt_text:
            return

        event.prompt_input.text = ""
        if prompt_text in [":q", "/quit", "/exit"]:
            self._begin_exit()
            return

        if prompt_text.startswith("!") and "\n" not in prompt_text and "\r" not in prompt_text:
            self._submit_shell_command(prompt_text[1:].lstrip())
            return

        self._submit_prompt(prompt_text)

    def _submit_shell_command(self, command: str) -> None:
        """Echo `!command` into the history, disable the input, and dispatch it to a worker
        thread (`_run_shell_command`) so a slow command can't block the UI. Only one shell
        command can be in flight at a time for this REPL: the input box stays disabled for the
        duration, exactly as it does for a model turn in `_submit_prompt`, so a user can't
        start a second one while the first is still running. The echoed `Static` is
        constructed with `markup=False`: `command` is arbitrary user input and must render
        verbatim rather than be parsed as Textual console markup.

        Like `_submit_prompt`, drops the submit if a turn or shell command is already running
        (`_turn_in_flight`) — the authoritative guard that keeps the two serial regardless of the
        racy `disabled` flag.
        """
        if self._turn_in_flight:
            return
        self._turn_in_flight = True

        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        input_widget.disabled = True

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(f"!{command}", classes="prompt", markup=False))
        history.scroll_end(animate=False)

        self._shell_cancel_event = threading.Event()
        self.refresh_bindings()
        self._run_shell_command(command, self._shell_cancel_event)

    @work(thread=True)
    def _run_shell_command(self, command: str, cancel_event: threading.Event) -> None:
        """Run `command` via `UserShellCommand` on a worker thread, streaming its combined
        stdout/stderr into the history as it arrives (mirroring `_send_prompt`'s progressive
        rendering of a streamed model response). `output_lock` serializes the stdout and
        stderr pump threads `UserShellCommand.run()` spawns internally — both call back into
        `handle_output` concurrently, and without a lock two racing calls could each mount
        their own output widget, or one line could clobber another's addition to `accumulated`.

        The shell binary and timeout come from `ProcessConfig` (`shell_command`,
        `shell_timeout_seconds`); a timeout or a Ctrl+C interrupt (`action_interrupt`, which
        sets `cancel_event`) both kill the process and are reported as an error in the history
        rather than raised, matching how a failed/aborted model turn is displayed.
        """
        output_widget: Static | None = None
        accumulated = ""
        output_lock = threading.Lock()

        def handle_output(delta_text: str) -> None:
            nonlocal accumulated, output_widget
            with output_lock:
                accumulated += delta_text
                if output_widget is None:
                    output_widget = self.call_from_thread(self._mount_shell_output_widget, accumulated)
                else:
                    self.call_from_thread(
                        self._update_shell_output_widget, output_widget, accumulated)

        error_message: str | None = None
        # Outer try/finally guarantees `_turn_in_flight` clears however this worker unwinds,
        # including a BaseException past the handlers below -- see `_send_prompt` for the rationale.
        try:
            try:
                _, _, rc = UserShellCommand(
                    command, shell_path=self._process_config.shell_command).run(
                        on_stdout=handle_output, on_stderr=handle_output,
                        timeout=self._process_config.shell_timeout_seconds, cancel_event=cancel_event)
            except ShellCommandTimedOut:
                timeout = self._process_config.shell_timeout_seconds
                error_message = f"Shell command timed out after {timeout:g}s; killed."
            except ShellCommandCancelled:
                error_message = "Shell command interrupted."
            else:
                if rc != 0:
                    error_message = f"Shell command exited with status {rc}."

            self.call_from_thread(self._finish_shell_command, error_message)
        finally:
            try:
                self.call_from_thread(self._ensure_turn_finished)
            except Exception:
                pass

    def _mount_shell_output_widget(self, initial_text: str) -> Static:
        """Mount a new `Static` widget for a streaming shell command's output and return it.

        Uses `Static` rather than the `Markdown` widget `_mount_response_widget` mounts for a
        streaming model reply: shell output is plain text, not markdown, and `Markdown`'s
        CommonMark rendering collapses a single newline within a paragraph into a soft line
        break (a space), which mangles multi-line command output. `Static` renders text
        verbatim, preserving every newline. Constructed with `markup=False` so a literal `[` in
        the output (e.g. an `[INFO]` log tag) can't be misread as the start of a Textual
        console markup tag and raise `MarkupError`.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget = Static(initial_text, markup=False)
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        return widget

    def _update_shell_output_widget(self, widget: Static, text: str) -> None:
        """Update a streaming shell command's output `Static` widget with the latest
        accumulated `text`, following the view to the bottom only if it was already pinned
        there before this growth (see `_history_pinned_to_bottom`) — so a user who's scrolled
        up to reread earlier output isn't yanked back down by every incoming chunk.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(text)
        self._scroll_if_pinned(history, was_pinned)

    def _finish_shell_command(self, error_message: str | None) -> None:
        """Show `error_message` (if any) in the history, then finish the shell command's
        "turn" exactly like a model turn (`_finish_turn`): scroll to the end, refresh the
        token tally, and re-enable/refocus the input box.
        """
        if error_message is not None:
            self._show_error(error_message)
        else:
            history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
            self._finish_turn(history, self._history_pinned_to_bottom)

    def clear_session(self) -> None:
        """Replace the active Session with a fresh one (new id, config re-read from disk
        with CLI flags re-applied on top), reset the visible history, and roll over the
        per-session log file if session logging is enabled for this REPL invocation.

        Tears down the outgoing `Session` first (`Session.close()`) so a live resource it
        registered a teardown for (e.g. `BashTool`'s persistent shell) doesn't leak past this
        call — nothing else references the outgoing `Session` afterward.

        The new session's `SessionConfig` is rebuilt by re-reading the config layers from
        disk (keeping just the session-scoped parts) and applying the CLI flags on top, so a
        config-file edit made between startup and this `/clear` takes effect and a `--model`
        (etc.) flag survives a `/clear` the same way it survived startup — see
        `apply_cli_flags_to_session` and docs/specs/process-and-session-config.md.
        `_apply_workspace_config` is then called with the same workspace as before to fold in
        the workspace-trust-driven parts (project-layer `readDirs`/`writeDirs` grants,
        `config_warnings`) the same way it does when trust is first resolved.
        """
        old_session: Session = self._session

        workspace = old_session.config.workspace
        old_session.close()

        # Re-read the config layers from disk into a fresh `SessionConfig` (only the
        # session-scoped parts), so a config change made after this process started is
        # picked up here rather than silently ignored. Then layer
        # the CLI flags on top, so a `--max-tool-calls-per-turn` (etc.) passed to this
        # invocation survives a `/clear` the same way it survived startup.
        reloaded_pc = load_process_config(
            config_flag_path=self._config_flag_path, cwd=workspace.path, workspace=workspace)
        self._process_config.session = reloaded_pc.session
        apply_cli_flags_to_session(self._process_config)
        new_session_config = self._process_config.session.model_copy()

        # Take note of any warnings / syntax errors raised when re-parsing config files.
        new_warnings: list[str] = reloaded_pc.config_warnings

        # The existing workspace is brought into the new session the same way it
        # does when trust is first resolved; see docs/specs/process-and-session-config.md.
        new_session_config.workspace = workspace

        # The choice of model is a "live" setting that the user may have been manipulating
        # throughout; we carry forward their choice from the prior session, here.
        new_session_config.model = old_session.config.model
        new_session_config.thinking_effort = old_session.config.thinking_effort
        new_session_config.thinking_enabled = old_session.config.thinking_enabled

        # Once the new session_config is ready, wrap it up into the new Session.
        self._session = Session(
            new_session_config,
            provider=old_session.provider,
            model_registry=old_session.model_registry,
            process_config=self._process_config,
            tool_registry=ToolRegistry(self._process_config, new_session_config),
        )

        if self._session_log_enabled:
            log_path = session_log_path(self._session.id)
            configure_logging(repl_mode=True, log_path=log_path)
            logger.debug("Cleared session; now logging to %s", log_path)

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.remove_children()
        history.mount(Static("Session cleared.", classes="notice"))

        # Display new config parser warnings after we've already cleared the history.
        for warning in new_warnings:
            self.show_notice(warning, error=True)

        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.clear_input_history()
        prompt_input.focus()
        self._update_status_bar()

    def _submit_prompt(self, prompt_text: str) -> None:
        """Echo `prompt_text` into the history, disable the input, and dispatch it. The echoed
        `Static` is constructed with `markup=False`: `prompt_text` is arbitrary user input and
        must render verbatim rather than be parsed as Textual console markup.

        Drops the submit outright if a turn or shell command is already running (`_turn_in_flight`):
        the input's `disabled` flag alone can't prevent a second submit that was queued before the
        first one disabled the box, so this authoritative check-and-set is what actually keeps turns
        serial (see `_turn_in_flight`).
        """
        if self._turn_in_flight:
            return
        self._turn_in_flight = True

        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        input_widget.disabled = True

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        prompt_widget = Static(prompt_text, classes="prompt", markup=False)
        history.mount(prompt_widget)
        history.scroll_end(animate=False)

        self._cancel_event = threading.Event()
        self.refresh_bindings()
        self._send_prompt(prompt_text, self._cancel_event)

    @work(thread=True)
    def _send_prompt(self, prompt_text: str, cancel_event: threading.Event) -> None:
        """Send `prompt_text` to the model on a worker thread so the UI stays responsive.

        Renders the response progressively as chunks stream in: a `Markdown` widget is
        mounted on the first chunk and updated on each subsequent one. Reasoning/thinking
        chunks, if the model sends any, are rendered the same way but behind a `<Thinking>`
        label and in italics, via a separate `Static` widget (not `Markdown`: reasoning text
        commonly spans multiple paragraphs, and Markdown's `*...*` emphasis syntax doesn't
        apply across blank-line-separated blocks, whereas Rich's `[italic]...[/italic]`
        console markup styles every line regardless). A structured `reasoning_details`
        payload, if the provider sends one, is rendered below the `<Thinking>` block as a
        one-line `<Reasoning>` indicator (see `_summarize_reasoning_details`) -- but only when
        it carries information the `<Thinking>` block can't show (e.g. an opaque/encrypted
        entry); a payload made entirely of plain-text entries is suppressed as redundant.

        A turn with tool calls is really a sequence of rounds under the hood (see
        `Session._dispatch_turn`): each round streams its own thinking/response text, then, if
        it ends in a tool-call request, `Session` dispatches those calls before starting the
        next round's stream. `on_tool_call` fires once per finished call, in between two
        rounds' streams, so seeing it is what marks a round boundary here — `round_index` is
        bumped on every such call, and `handle_chunk`/`handle_thinking_chunk` compare it
        against the round their current widget belongs to, starting a fresh widget rather than
        appending to the previous round's whenever it's moved on. Without this, one round's
        widget would keep absorbing every later round's text too, reading as a single
        ever-growing block that swallows the tool calls mounted in between rather than as
        separate, time-ordered blocks.

        If `cancel_event` is set (via Escape / `action_abort_response`) before the response
        finishes, `Session.send_turn()` raises `ResponseAborted`; the echoed prompt and any
        widgets already mounted for this turn's partial response/thinking/tool calls are left
        in place (`Session` keeps the same content in history, tagged `"aborted"` — see
        `_handle_aborted_response`) rather than torn down, and the input box is left empty for
        the next message rather than repopulated with this one. `response_widget`/
        `thinking_widget` at that point necessarily belong to the round that was still
        streaming when Escape fired, matching the single round's worth of content `Session`
        keeps for it.
        """
        response_widget: Markdown | None = None
        accumulated = ""
        response_round: int | None = None
        thinking_widget: Static | None = None
        thinking_accumulated = ""
        thinking_round: int | None = None
        reasoning_details_widget: Static | None = None
        reasoning_details_round: int | None = None
        round_index = 0

        def handle_chunk(delta_text: str) -> None:
            nonlocal accumulated, response_widget, response_round
            if response_round != round_index:
                response_widget = None
                accumulated = ""
                response_round = round_index
            accumulated += delta_text
            if response_widget is None:
                response_widget = self.call_from_thread(self._mount_response_widget, accumulated)
            else:
                self.call_from_thread(self._update_response_widget, response_widget, accumulated)

        def handle_thinking_chunk(delta_text: str) -> None:
            nonlocal thinking_accumulated, thinking_widget, thinking_round
            if thinking_round != round_index:
                thinking_widget = None
                thinking_accumulated = ""
                thinking_round = round_index
            thinking_accumulated += delta_text
            if thinking_widget is None:
                thinking_widget, _ = self.call_from_thread(
                    self._mount_thinking_widget, thinking_accumulated)
            else:
                self.call_from_thread(
                    self._update_thinking_widget, thinking_widget, thinking_accumulated)

        def handle_reasoning_details_chunk(entries: list[dict[str, Any]]) -> None:
            nonlocal reasoning_details_widget, reasoning_details_round
            if reasoning_details_round != round_index:
                reasoning_details_widget = None
                reasoning_details_round = round_index
            text = _summarize_reasoning_details(entries)
            if text is None:
                return
            if reasoning_details_widget is None:
                reasoning_details_widget, _ = self.call_from_thread(
                    self._mount_reasoning_details_widget, text)
            else:
                self.call_from_thread(
                    self._update_reasoning_details_widget, reasoning_details_widget, text)

        def handle_tool_call_started(event: ToolCallStartedEvent) -> None:
            summary_text = self._render_tool_call_summary(event.name, event.args)
            self.call_from_thread(
                self._mount_running_tool_call_widget, event.call_id, summary_text)

        def handle_tool_call(event: ToolCallEvent) -> None:
            nonlocal round_index
            summary_text, detail_text = self._render_tool_call(event)
            running_widget = self._running_tool_call_widgets.pop(event.call_id, None)
            if running_widget is not None:
                self.call_from_thread(
                    self._finalize_running_tool_call_widget,
                    running_widget, summary_text, detail_text)
            else:
                self.call_from_thread(self._mount_tool_call_widget, summary_text, detail_text)
            round_index += 1

        # The outer try/finally guarantees `_turn_in_flight` is cleared however this worker
        # unwinds -- including a BaseException (e.g. asyncio.CancelledError / worker cancellation)
        # that slips past `except Exception` below and would otherwise leave the flag stuck True
        # forever. See `_ensure_turn_finished` and docs/specs/interrupt-and-liveness-watchdog.md.
        try:
            try:
                response_text = self._session.send_turn(prompt_text, TurnEventHandlers(
                    on_chunk=handle_chunk, on_thinking_chunk=handle_thinking_chunk,
                    on_reasoning_details=handle_reasoning_details_chunk,
                    cancel_event=cancel_event,
                    on_tool_call_limit_reached=self._on_tool_call_limit_reached,
                    on_permission_ask=self._on_permission_ask,
                    on_ask_user_questions=self._on_ask_user_questions,
                    on_escalate_privileges=self._on_escalate_privileges,
                    on_tool_call_started=handle_tool_call_started,
                    on_tool_call=handle_tool_call))
            except ResponseAborted:
                self.call_from_thread(
                    self._handle_aborted_response, response_widget, accumulated,
                    thinking_widget, thinking_accumulated)
            except Exception as exc:
                self.call_from_thread(self._show_error, str(exc))
            else:
                if response_widget is not None:
                    self.call_from_thread(
                        self._finalize_streamed_response, response_widget, response_text)
                else:
                    self.call_from_thread(self._show_response, response_text)
        finally:
            # No-op on the normal path (a handler above already finished the turn); only does
            # anything when the worker unwound without reaching one. Guarded because the event
            # loop may already be gone during teardown.
            try:
                self.call_from_thread(self._ensure_turn_finished)
            except Exception:
                pass

    def _mount_response_widget(self, initial_text: str) -> Markdown:
        """Mount a new `Markdown` widget for a streaming response and return it. Also refreshes
        the status bar's token tally (see `_update_status_bar`): a new placeholder `Message`
        with its own `num_tokens` was just appended to the session for this chunk (see
        `Session._send_and_receive.handle_chunk`), so the footer should reflect it immediately
        rather than only once the whole turn finishes.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget = Markdown(initial_text, classes="response")
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()
        return widget

    def _update_response_widget(self, widget: Markdown, text: str) -> None:
        """Update a streaming response `Markdown` widget with the latest accumulated `text`,
        following the view to the bottom only if it was already pinned there before this change
        (see `_history_pinned_to_bottom`), so a user who's scrolled up to reread earlier output
        isn't yanked back down by every incoming chunk. `widget` is always still the last thing
        mounted in the history at this point: `_send_prompt` starts a fresh widget rather than
        reusing this one once a round boundary (a tool call) has passed, so there's nothing
        mounted after it left to get stuck behind. Also refreshes the status bar (see
        `_mount_response_widget`): the placeholder message's `num_tokens` grew along with
        `text`.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(text)
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()

    def _mount_thinking_widget(self, initial_text: str) -> tuple[Static, Static]:
        """Mount a left-justified `<Thinking>` label followed by an italicized `Static`
        widget for a streaming thinking block, and return `(body_widget, label_widget)`. The
        body is styled italic via the `.thinking-body` CSS class rather than markup, and
        constructed with `markup=False`: model thinking text is arbitrary and must render
        verbatim, not be parsed as Textual console markup (an unescaped `[` in it can
        otherwise be misread as the start of a markup tag and raise `MarkupError`). Also
        refreshes the status bar (see `_mount_response_widget`): a new `role="thinking"`
        placeholder message with its own `num_tokens` was just appended to the session.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        label_widget = Static(THINKING_LABEL, classes="thinking-label")
        history.mount(label_widget)
        widget = Static(initial_text, classes="thinking-body", markup=False)
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()
        return widget, label_widget

    def _update_thinking_widget(self, widget: Static, text: str) -> None:
        """Update a streaming `<Thinking>` `Static` widget with the latest accumulated `text`,
        following the view to the bottom only if it was already pinned there before this
        change (see `_update_response_widget`) — the label mounted alongside `widget` never
        changes after being set, so only the body needs updating here. Also refreshes the
        status bar: the placeholder message's `num_tokens` grew along with `text`.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(text)
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()

    def _mount_reasoning_details_widget(self, text: str) -> tuple[Static, Static]:
        """Mount a left-justified `<Reasoning>` label followed by an italicized `Static`
        widget showing `text` (see `_summarize_reasoning_details`), mirroring
        `_mount_thinking_widget`'s label/body split and `markup=False` rationale, and return
        `(body_widget, label_widget)`. Also refreshes the status bar: a new `role="thinking"`
        placeholder message's `reasoning_details` was just set on the session (see
        `Session._send_and_receive.handle_reasoning_details_chunk`).
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        label_widget = Static(REASONING_DETAILS_LABEL, classes="reasoning-details-label")
        history.mount(label_widget)
        widget = Static(text, classes="reasoning-details-body", markup=False)
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()
        return widget, label_widget

    def _update_reasoning_details_widget(self, widget: Static, text: str) -> None:
        """Update a `<Reasoning>` `Static` widget with the latest `text` (see
        `_summarize_reasoning_details`), mirroring `_update_thinking_widget`."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(text)
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()

    def _render_tool_call(self, event: ToolCallEvent) -> tuple[str, str]:
        """Render `event` as `(summary_text, detail_text)` — see `_render_tool_result`, which
        does the actual work from `event`'s individual fields. Split out so
        `_render_restored_tool_call` (reconstructing a finished call from persisted `Message`s
        rather than a live `ToolCallEvent`) can share the same rendering logic.
        """
        return self._render_tool_result(
            event.name, event.args, event.result, event.error, event.raw_arguments)

    def _render_tool_result(
        self, name: str, args: dict[str, Any], result: Any, error: str | None,
        raw_arguments: str | None,
    ) -> tuple[str, str]:
        """Render one finished tool call as `(summary_text, detail_text)` via its tool's
        `summary()`/`detail_view()` — instantiating a fresh `Tool` purely to call these pure
        methods (`ToolRegistry.instantiate_tool()` is already a cheap, no-shared-state
        operation; see docs/adrs/tool-registry-instantiates-a-fresh-tool-per-call.md) — or via
        the shared default formatters if `name` isn't a registered tool (e.g. a hallucinated
        tool name, which `Session._run_tool_calls` already turned into `error`).

        `raw_arguments is not None` means the model's `arguments` string failed to parse as
        JSON before any tool ever ran (see `ToolCallEvent.raw_arguments`); that's rendered via
        `default_invalid_tool_call_summary()`/`default_invalid_tool_call_detail()` instead,
        showing the raw malformed text rather than the always-empty `args`.
        """
        if raw_arguments is not None:
            assert error is not None
            return (default_invalid_tool_call_summary(name, error),
                    default_invalid_tool_call_detail(name, raw_arguments, error))
        registry = self._session.tool_registry
        try:
            if registry is None:
                raise KeyError(name)
            tool = registry.instantiate_tool(name)
        except (KeyError, NoSuchToolException):
            return (default_tool_call_summary(name, args, error),
                    default_tool_call_detail(name, args, result, error))
        return (tool.summary(args, result, error), tool.detail_view(args, result, error))

    def _mount_tool_call_widget(self, summary_text: str, detail_text: str) -> tuple[ToolCallStatic, Static]:
        """Mount a left-justified `<Tool use>` label (styled via the `.tool-call-label` CSS
        class, matching `<Thinking>`'s label/body split in `_mount_thinking_widget`) followed
        by a `ToolCallStatic` for one finished tool call, and return `(widget, label_widget)`.
        Applies the current global detail-shown state (see `action_toggle_tool_call_detail`)
        so a call rendered while detail view is already active shows detail immediately rather
        than a summary the user would have to toggle past. Also refreshes the status bar (see
        `_mount_response_widget`): `Session._run_tool_calls` has already appended this call's
        `role="tool_response"` message (with its own `num_tokens`) by the time
        `callbacks.on_tool_call` — and so this method — runs.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        label_widget = Static(TOOL_USE_LABEL, classes="tool-call-label")
        history.mount(label_widget)
        widget = ToolCallStatic(summary_text, detail_text)
        widget.set_detail_shown(self._tool_call_detail_shown)
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        self._tool_call_widgets.append(widget)
        self._update_status_bar()
        return widget, label_widget

    def _render_tool_call_summary(self, name: str, args: dict[str, Any]) -> str:
        """Render a tool call's pre-execution summary (the label shown in the running
        indicator) by calling `Tool.summary(args)` with no `result` or `error`. Every
        tool's `summary()` produces a meaningful label when called this way -- e.g.
        ``Bash: <intent>\\n$ <command>`` for BashTool -- since the result/error suffix is
        only appended when those parameters are non-`None`. Falls back to the module-level
        `default_tool_call_summary()` if the tool name isn't registered.
        """
        registry = self._session.tool_registry
        try:
            if registry is None:
                raise KeyError(name)
            tool = registry.instantiate_tool(name)
        except (KeyError, NoSuchToolException):
            return default_tool_call_summary(name, args, None)
        return tool.summary(args)

    def _mount_running_tool_call_widget(
        self, call_id: str, summary_text: str,
    ) -> RunningToolCallStatic:
        """Mount a ``<Tool use>`` label followed by a `RunningToolCallStatic` showing the
        tool's pre-execution label with a crawling ``Running...`` animation. Stores the
        widget in `_running_tool_call_widgets` keyed by `call_id` so the eventual
        `handle_tool_call` completion callback can finalize it in place rather than mounting
        a duplicate. Also tracks it in `_tool_call_widgets` for the Ctrl+O detail toggle.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        label_widget = Static(TOOL_USE_LABEL, classes="tool-call-label")
        history.mount(label_widget)
        widget = RunningToolCallStatic(summary_text)
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        self._tool_call_widgets.append(widget)
        self._running_tool_call_widgets[call_id] = widget
        self._update_status_bar()
        return widget

    def _finalize_running_tool_call_widget(
        self, widget: RunningToolCallStatic, summary_text: str, detail_text: str,
    ) -> None:
        """Stop the running indicator animation and replace it with the final
        summary/detail text. Called from `handle_tool_call` via `call_from_thread` when a
        tool call completes -- the widget was mounted earlier by
        `_mount_running_tool_call_widget` and is already in the history scroll and the
        Ctrl+O tracking list."""
        widget.finalize(summary_text, detail_text)
        widget.set_detail_shown(self._tool_call_detail_shown)
        self._update_status_bar()

    def _mount_restored_history(self, messages: list[ChatMessage]) -> None:
        """Re-render `messages` — a previous session's history, already loaded onto
        `self._session` by `_maybe_restore_last_session` — into the history scroll, so a
        restored conversation reads the same way it would have live: one `Static`/`Markdown`/
        `ToolCallStatic` per user/assistant/thinking/tool-use message, in original order, via
        the same `_mount_response_widget`/`_mount_thinking_widget`/`_mount_tool_call_widget`
        helpers a live turn uses. A `"thinking"` message's `reasoning_details`, if it carries
        one, is rendered right after it via `_mount_reasoning_details_widget`, subject to the
        same `_summarize_reasoning_details()` suppression a live turn applies.
        `role="system"`/`"tool_defs"` bookkeeping messages are skipped, matching how they're
        never rendered live either (see `Session._ensure_system_message`/
        `_ensure_tool_defs_message`). A `role="tool_response"` message is rendered together
        with its matching `role="tool_use"` entry, via `_render_restored_tool_call`, rather
        than on its own.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        responses_by_call_id = {
            message.tool_call_id: message for message in messages
            if message.role == "tool_response" and message.tool_call_id is not None
        }
        for message in messages:
            if message.role == "user":
                history.mount(Static(message.content, classes="prompt", markup=False))
            elif message.role == "assistant":
                text = message.content
                if message.processing_state == "aborted":
                    text = f"{text}\n\n*(interrupted)*"
                self._mount_response_widget(text)
            elif message.role == "thinking":
                text = message.content
                if message.processing_state == "aborted":
                    text = f"{text}\n\n(interrupted)"
                self._mount_thinking_widget(text)
                if message.reasoning_details:
                    reasoning_details_text = _summarize_reasoning_details(message.reasoning_details)
                    if reasoning_details_text is not None:
                        self._mount_reasoning_details_widget(reasoning_details_text)
            elif message.role == "tool_use":
                for call in message.tool_calls or []:
                    summary_text, detail_text = self._render_restored_tool_call(
                        call, responses_by_call_id.get(call.id))
                    self._mount_tool_call_widget(summary_text, detail_text)
        history.mount(Static(f"Restored previous session ({len(messages)} messages).", classes="notice"))
        history.scroll_end(animate=False)

    def _render_restored_tool_call(
        self, call: ToolCallRequest, response: ChatMessage | None,
    ) -> tuple[str, str]:
        """Reconstruct a finished tool call's `(summary_text, detail_text)` for
        `_mount_restored_history` from persisted `Message`s alone — `call.arguments` (the
        model's raw JSON-encoded arguments) and `response.content` (see
        `_format_tool_response_content` in `klorb.session`, the encoding
        `Session._run_tool_calls` used to fold a live call's `(result, error)` into one string,
        the only form either survives in once persisted).

        Best-effort, not lossless: a successful string result that happens to start with
        `"Error: "` is indistinguishable here from an actual failure, since the two are folded
        into the same `content` string on write; every other shape round-trips exactly. A
        missing `response` (a truncated or hand-edited save file) renders as a call with a
        `None` result rather than raising.
        """
        try:
            args = json.loads(call.arguments) if call.arguments else {}
            if not isinstance(args, dict):
                raise ValueError("tool call arguments must decode to a JSON object")
        except (json.JSONDecodeError, ValueError) as json_exc:
            parse_error = f"Invalid JSON in tool call arguments: {json_exc}"
            return self._render_tool_result(call.name, {}, None, parse_error, call.arguments)

        result: Any = None
        error: str | None = None
        if response is not None:
            if response.content.startswith("Error: "):
                error = response.content[len("Error: "):]
            else:
                try:
                    result = json.loads(response.content)
                except json.JSONDecodeError:
                    result = response.content
        return self._render_tool_result(call.name, args, result, error, None)

    async def _confirm_tool_call_limit(self, message: str) -> bool:
        """Show `ToolCallLimitScreen` with `message` and wait for the user's yes/no answer.

        Must be run on the app's own event loop, since it awaits the screen's dismissal —
        `_on_tool_call_limit_reached` is what the worker thread actually calls, via
        `call_from_thread`, to get here.
        """
        return await self.push_screen_wait(ToolCallLimitScreen(message))

    def _on_tool_call_limit_reached(self, message: str) -> bool:
        """`Session`'s `on_tool_call_limit_reached` callback: block the worker thread running
        `Session.send_turn()` until the user answers `ToolCallLimitScreen(message)`.
        """
        # mypy can't solve App.call_from_thread's `CallThreadReturnType` TypeVar against a
        # `Callable[[str], Coroutine[Any, Any, bool]]` argument (a stub-modeling limitation,
        # not a real type error: Textual's own runtime `invoke()` awaits coroutine callbacks
        # like any other `Callable[..., T | Awaitable[T]]` argument, and this is exercised by
        # test_tui_repl.py's ToolCallLimitScreen tests).
        callback = self._confirm_tool_call_limit
        confirmed: bool = self.call_from_thread(callback, message)  # type: ignore[arg-type]
        return confirmed

    def _enter_interaction_mode(self) -> Vertical:
        """Disable and visually mute/collapse the prompt input while an interaction panel (a
        permission ask or an ask-user-questions prompt) is active, returning the
        `#interaction-panel` container for the caller to mount that panel's content into.

        Collapsing to `height: 1` (via the `interaction-active` CSS class — see `ReplApp.CSS`)
        shrinks any in-progress multi-line draft back down to its default single-row size
        rather than leaving it competing with the panel for vertical space; the draft text
        itself is untouched underneath, so `_exit_interaction_mode` restoring `height: auto`
        brings it back exactly as the user left it.
        """
        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.disabled = True
        prompt_input.add_class("interaction-active")
        return self.query_one(f"#{INTERACTION_PANEL_ID}", Vertical)

    def _exit_interaction_mode(self) -> None:
        """Un-mute and un-collapse the prompt input once an interaction panel is dismissed, but
        leave it *disabled*: an interaction panel only ever appears part-way through a turn that
        is still running (a permission ask, ask-user-questions, or escalate-privileges callback
        fired from inside `Session.send_turn`), so the input must stay locked until the whole turn
        finishes and `_finish_turn` re-enables it. Re-enabling here instead would let the user
        submit a second prompt mid-turn, launching a second concurrent `_send_prompt` worker whose
        own tool calls would mount interaction panels into `#interaction-panel` alongside this
        turn's — the "stacked panels" bug this guards against. Focus is intentionally not moved
        onto the (still-disabled) input either; the removed panel's focus is reassigned by Textual
        on its own."""
        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.remove_class("interaction-active")

    def _record_interaction_history(self, header_text: str, body: str, decision_text: str) -> None:
        """Leave a permanent record of a just-finished permission ask or ask-user-questions
        exchange in the history scroll: `header_text` (e.g. `"Permission requested: Run
        command"` or the question's own `"Question 2 of 3 · Auth"` header), `body` (the
        command/path/detail or question text the panel showed), and `decision_text` (the user's
        rendered choice) — so scrolling back through the session shows not just that an
        approval happened, but what was asked and what was decided. Constructed with
        `markup=False`: `body` can be arbitrary command text or file paths, which must render
        verbatim.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        history.mount(Static(INTERACTION_RECORD_LABEL, classes="interaction-record-label"))
        history.mount(Static(header_text, classes="interaction-record-body", markup=False))
        history.mount(Static(body, classes="interaction-record-body", markup=False))
        history.mount(Static(
            f"Decision: {decision_text}", classes="interaction-record-decision", markup=False))
        self._scroll_if_pinned(history, was_pinned)

    def _register_interaction_future(
        self, future: "asyncio.Future[_InteractionResult]", teardown_default: _InteractionResult,
    ) -> Callable[[_InteractionResult], None]:
        """Wire a just-created interaction panel's decision `future` for safe resolution, returning
        the guarded `on_dismiss` callback the panel reports its result through.

        The returned resolver is idempotent: a second dismiss (e.g. a stray keypress landing
        between the panel resolving and `_confirm_*` unmounting it), or a teardown resolving the
        future first, is a no-op rather than an `asyncio.InvalidStateError`. It also registers
        `_release_pending_interaction` so a teardown/abort path can resolve this same future with
        `teardown_default` (deny / cancelled), unblocking the worker thread parked in
        `App.call_from_thread` awaiting the decision and freeing `_interaction_lock`. The caller
        must clear `_release_pending_interaction` in its own `finally` once the await completes."""
        def resolve(result: _InteractionResult) -> None:
            if not future.done():
                future.set_result(result)
        self._release_pending_interaction = lambda: resolve(teardown_default)
        return resolve

    async def _confirm_permission_ask(self, ask_ctx: PermissionAskContext) -> PermissionDecision:
        """Mount a `PermissionAskPanel` for `ask_ctx` into `#interaction-panel` and wait for the
        user's choice.

        Must be run on the app's own event loop, since it awaits the panel's dismissal —
        `_on_permission_ask` is what the worker thread actually calls, via `call_from_thread`,
        to get here. `compute_grant_paths()`/`compute_command_grant_patterns()` are recomputed
        here (rather than threaded in from wherever `ask_ctx` was built) purely so the panel can
        show the directory/command pattern a persistent grant would actually cover; both are
        pure and read-only, so calling them again inside `_on_permission_ask`/
        `apply_permission_grant`/`apply_command_permission_grant` afterwards is safe and cheap.
        Seeds the panel's grid cursor from `_last_permission_action`/`_last_permission_scope`
        and updates both from the returned decision, so a run of several asks for one compound
        tool call starts each next prompt where the previous one left off — unless
        `klorb.permissions.risk_classifier.resolve_item_risk_assessment` rates this item at or
        above `tools.bash.riskClassifier.tooRiskyThreshold`, which pre-selects `Deny, once`
        instead (still just a starting cursor position; every cell stays reachable and
        confirmable regardless). `ReplApp` itself never constructs an `ItemRiskAssessment` or
        talks to the classifier directly — `resolve_item_risk_assessment` owns the gating,
        batching (across a compound command's several serially-asked items), and caching, so
        this same call would work identically from any other UI layer driving `Session`. Once the
        user's own `PermissionDecision` comes back, `klorb.permissions.risk_classifier.
        record_decision_history` records it (command text plus decision) into this `session`'s
        bounded history, so a later `resolve_item_risk_assessment` call this session can use it as
        calibration context — see that function's own docstring.
        """
        risk_assessment = resolve_item_risk_assessment(
            ask_ctx, session=self._session, process_config=self._process_config)

        granted_paths = None
        granted_command_patterns = None
        if ask_ctx.path is not None:
            granted_paths = compute_grant_paths(
                self._session.config.read_dirs, self._session.config.write_dirs,
                self._session.config.workspace.path, ask_ctx.path, ask_ctx.is_write)
        elif ask_ctx.command is not None:
            if risk_assessment is not None and risk_assessment.suggested_pattern:
                granted_command_patterns = [risk_assessment.suggested_pattern]
            else:
                granted_command_patterns = compute_command_grant_patterns(
                    self._session.config.command_rules, ask_ctx.command)

        decision_future: asyncio.Future[PermissionDecision] = asyncio.get_running_loop().create_future()
        preview_wrap_width = max(
            _MIN_COMMAND_PREVIEW_WRAP_WIDTH, self.size.width - _COMMAND_PREVIEW_WIDTH_PADDING)
        initial_action = self._last_permission_action
        initial_scope = self._last_permission_scope
        if (
            risk_assessment is not None
            and risk_assessment.risk_score >= self._process_config.bash_risk_classifier_too_risky_threshold
        ):
            initial_action, initial_scope = "deny", "once"
        panel = PermissionAskPanel(
            ask_ctx, granted_paths=granted_paths, granted_command_patterns=granted_command_patterns,
            initial_action=initial_action, initial_scope=initial_scope,
            risk_score=risk_assessment.risk_score if risk_assessment is not None else None,
            risk_rationale=risk_assessment.rationale if risk_assessment is not None else None,
            preview_wrap_width=preview_wrap_width,
            on_dismiss=self._register_interaction_future(
                decision_future, PermissionDecision(action="deny", scope="once")))

        try:
            async with self._interaction_lock:
                panel_container = self._enter_interaction_mode()
                await panel_container.mount(panel)
                decision = await decision_future
                await panel.remove()
                self._exit_interaction_mode()
        finally:
            self._release_pending_interaction = None

        self._last_permission_action = decision.action
        self._last_permission_scope = decision.scope
        record_decision_history(
            ask_ctx, decision, session=self._session, process_config=self._process_config)
        # TODO(aaron): once a structured audit log for permission decisions exists, record an
        # entry here pairing `ask_ctx` (this command/path being asked about) with the user's own
        # `decision` -- this is the "this command _____ got this decision: _____" injection
        # point (a separate concern from pairing a command with its own risk assessment, whose
        # injection point is in klorb.permissions.risk_classifier.classify_command_risk).
        self._record_interaction_history(
            panel.header_text(), format_ask_context_body(ask_ctx), format_permission_decision(decision))
        return decision

    def _on_permission_ask(self, ask_ctx: PermissionAskContext) -> PermissionDecision:
        """`Session`'s `on_permission_ask` callback: block the worker thread running
        `Session.send_turn()` until the user answers `PermissionAskPanel`, then return that
        choice as-is. Applying (and, for "workspace"/"homedir", persisting to disk) any grant
        the choice implies is `Session`'s own responsibility —
        `Session._retry_after_permission_decision`, via
        `klorb.permissions.grant.apply_permission_grant` — using the `ProcessConfig` reference
        `ReplApp` gave it at construction time; this callback only needs to surface the user's
        decision, not act on it.
        """
        # See the type-ignore note on `_on_tool_call_limit_reached` above; same mypy limitation.
        callback = self._confirm_permission_ask
        decision: PermissionDecision = self.call_from_thread(callback, ask_ctx)  # type: ignore[arg-type]
        return decision

    async def _confirm_ask_user_questions(
        self, ask_ctx: AskUserQuestionsItemContext,
    ) -> AskUserQuestionsAnswer:
        """Mount an `AskUserQuestionsPanel` for one question into `#interaction-panel` and wait
        for the user's answer.

        Must be run on the app's own event loop, since it awaits the panel's dismissal —
        `_on_ask_user_questions` is what the worker thread actually calls, via
        `call_from_thread`, to get here.
        """
        answer_future: asyncio.Future[AskUserQuestionsAnswer] = asyncio.get_running_loop().create_future()
        panel = AskUserQuestionsPanel(
            ask_ctx, on_dismiss=self._register_interaction_future(
                answer_future, AskUserQuestionsAnswer(cancelled=True)))

        try:
            async with self._interaction_lock:
                panel_container = self._enter_interaction_mode()
                await panel_container.mount(panel)
                answer = await answer_future
                await panel.remove()
                self._exit_interaction_mode()
        finally:
            self._release_pending_interaction = None

        self._record_interaction_history(
            panel.header_text(), ask_ctx.question, format_ask_user_questions_answer(answer))
        return answer

    def _on_ask_user_questions(self, ask_ctx: AskUserQuestionsItemContext) -> AskUserQuestionsAnswer:
        """`Session`'s `on_ask_user_questions` callback: block the worker thread running
        `Session.send_turn()` until the user answers `AskUserQuestionsPanel` for this one
        question, then return that answer as-is — `Session._resolve_ask_user_questions` calls
        this once per question in an `AskUserQuestionsRequired` batch and assembles the
        results itself.
        """
        # See the type-ignore note on `_on_tool_call_limit_reached` above; same mypy limitation.
        callback = self._confirm_ask_user_questions
        answer: AskUserQuestionsAnswer = self.call_from_thread(callback, ask_ctx)  # type: ignore[arg-type]
        return answer

    async def _confirm_escalate_privileges(
        self, escalate_ctx: EscalatePrivilegesContext,
    ) -> EscalatePrivilegesDecision:
        """Mount an `EscalatePrivilegesPanel` for `escalate_ctx` into `#interaction-panel` and
        wait for the user's choice.

        Must be run on the app's own event loop, since it awaits the panel's dismissal \u2014
        `_on_escalate_privileges` is what the worker thread actually calls, via
        `call_from_thread`, to get here.
        """
        decision_future: asyncio.Future[EscalatePrivilegesDecision] = (
            asyncio.get_running_loop().create_future())
        panel = EscalatePrivilegesPanel(
            escalate_ctx, on_dismiss=self._register_interaction_future(
                decision_future, EscalatePrivilegesDecision(approved=False)))

        try:
            async with self._interaction_lock:
                panel_container = self._enter_interaction_mode()
                await panel_container.mount(panel)
                decision = await decision_future
                await panel.remove()
                self._exit_interaction_mode()
        finally:
            self._release_pending_interaction = None

        self._record_interaction_history(
            panel.header_text(), escalate_ctx.description,
            format_escalate_privileges_decision(decision))
        return decision

    def _on_escalate_privileges(
        self, escalate_ctx: EscalatePrivilegesContext,
    ) -> EscalatePrivilegesDecision:
        """`Session`'s `on_escalate_privileges` callback: block the worker thread running
        `Session.send_turn()` until the user answers `EscalatePrivilegesPanel`, then return
        that decision as-is — `Session._resolve_escalate_privileges` records the approved
        scope into `SessionConfig.approved_scopes` itself; this callback only surfaces the
        user's decision, not act on it.
        """
        # See the type-ignore note on `_on_tool_call_limit_reached` above; same mypy limitation.
        callback = self._confirm_escalate_privileges
        decision: EscalatePrivilegesDecision = self.call_from_thread(
            callback, escalate_ctx)  # type: ignore[arg-type]
        return decision

    def _finalize_streamed_response(self, widget: Markdown, response_text: str) -> None:
        """Reconcile a streamed `Markdown` widget with the final response and finish the turn."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(response_text)
        self._finish_turn(history, was_pinned)

    def _show_response(self, response_text: str) -> None:
        """Append a model response to the history and re-enable the input box."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        history.mount(Markdown(response_text, classes="response"))
        self._finish_turn(history, was_pinned)

    def _show_error(self, message: str) -> None:
        """Append an error message to the history and re-enable the input box. Constructed
        with `markup=False`: `message` is often an exception's text (e.g. `str(OSError(...))`
        can read `[Errno 2] ...`), which must render verbatim rather than be parsed as Textual
        console markup.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        history.mount(Static(f"Error: {message}", classes="error", markup=False))
        self._finish_turn(history, was_pinned)

    def _handle_aborted_response(
        self, response_widget: Markdown | None, response_text: str,
        thinking_widget: Static | None, thinking_text: str,
    ) -> None:
        """Leave the echoed prompt and every widget mounted for the aborted turn in place
        (`Session` keeps the same content in history, tagged `processing_state="aborted"`,
        rather than discarding it), tagging whichever of the response/thinking widgets was
        still streaming when Escape fired with an "(interrupted)" marker. If neither had
        started streaming yet (the turn was interrupted before its first chunk, e.g. during an
        earlier round's tool-call dispatch), mount a standalone marker instead so the
        interruption is still visible. The input box is left empty rather than repopulated
        with the original prompt, since that prompt is now a real, if incomplete, turn in
        history rather than a draft to resend.
        """
        if response_widget is not None:
            response_widget.update(f"{response_text}\n\n*(interrupted)*")
        elif thinking_widget is not None:
            thinking_widget.update(f"{thinking_text}\n\n(interrupted)")
        else:
            history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
            history.mount(Static("(interrupted)", classes="interrupted"))

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        self._finish_turn(history, self._history_pinned_to_bottom)

    def _scroll_if_pinned(self, history: VerticalScroll, was_pinned: bool) -> None:
        """Scroll `history` to its end iff `was_pinned` — `_history_pinned_to_bottom`'s value
        captured immediately before the content that just changed was applied — so streaming
        updates follow the user to the bottom only when they hadn't already scrolled away from
        it.
        """
        if was_pinned:
            history.scroll_end(animate=False)

    def _finish_turn(self, history: VerticalScroll, was_pinned: bool) -> None:
        """Scroll the history into view (iff `was_pinned`, see `_scroll_if_pinned`), refresh the
        token tally, and hand focus back to the input box. Also clears the in-flight turn's
        cancel event and pending-prompt tracking (model turn) and the shell command's cancel
        event (`_run_shell_command`), since neither Escape nor Ctrl+C has anything left to
        abort/interrupt once a turn is done (successfully, in error, or aborted) — shared by
        both a model turn and a shell command, since only one of the two is ever in flight at a
        time. Clearing `_turn_in_flight` here (the terminal state of every model turn and shell
        command) is what lets the next submit through, and resetting `_interrupt_notice_shown`
        re-arms the one-time `Interrupting…` notice for the next turn.

        Reached on every path where `Session.send_turn`/the shell command returns or raises, via a
        terminal handler; `_send_prompt`/`_run_shell_command` additionally call it from a `finally`
        (through `_ensure_turn_finished`) so a `BaseException` unwind of the worker still gets here.
        Idempotent enough for that backstop: a second call after the flag is already cleared just
        re-does harmless UI resets.

        If a quit was requested while this turn was still running (`_exit_requested`, set by
        `_begin_exit`), the deferred `self.exit()` happens here — now that the worker thread has
        actually finished and can no longer be stranded by the event loop stopping out from under
        it. Runs last, after the turn is fully wound down.
        """
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()
        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        input_widget.disabled = False
        input_widget.focus()
        self._cancel_event = None
        self._shell_cancel_event = None
        self._turn_in_flight = False
        self._interrupt_notice_shown = False
        self.refresh_bindings()
        if self._exit_requested:
            self.exit()


def _handle_repl_crash(app: ReplApp, crash_tee: CrashLogTee) -> None:
    """Called by `run_repl()` once `App.run()` returns with `app.return_code == 1` —
    `App._handle_exception` sets that on an unhandled exception, and never raises it back out
    of `run()` itself (Textual prints the traceback and exits the app instead).

    Prints a pointer to the crash log file `crash_tee` captured (see `CrashLogTee`), or a
    fallback line if the file couldn't be opened. Then, if the crashed session's workspace is
    trusted, also saves its state via `klorb.workspace.last_session.write_last_session` — the
    same mechanism `ReplApp._quit_after_maybe_saving` uses for a normal, user-confirmed quit —
    since there's no modal to confirm through once the app has already crashed and the
    conversation would otherwise just be lost. Uses `app._session`, the app's live session at
    crash time, rather than whatever `Session` `run_repl()` was originally called with, since
    `/clear` (and workspace-trust changes) can replace or mutate it over the app's lifetime.
    """
    log_path = crash_tee.opened_log_path()
    if log_path is not None:
        print(f"klorb crashed; full stack trace written to {log_path}", file=sys.stderr)
    else:
        print("klorb crashed; could not write a crash log file.", file=sys.stderr)

    live_session = app._session
    if not live_session.config.workspace.trusted:
        return
    try:
        write_last_session(
            live_session.config.workspace, live_session.config, live_session.messages,
            statistics=live_session.statistics)
    except OSError:
        logger.warning("Could not save session state on crash.", exc_info=True)
        print("klorb crashed; could not save session state.", file=sys.stderr)
    else:
        print(
            f"klorb crashed; session state saved to "
            f"{last_session_path(live_session.config.workspace)}",
            file=sys.stderr)


def run_repl(
    session: Session | None = None,
    process_config: ProcessConfig | None = None,
    initial_message: str | None = None,
    session_log_enabled: bool = True,
    trust_manager: TrustManager | None = None,
    config_flag_path: Path | None = None,
) -> None:
    """Launch the interactive klorb REPL, optionally submitting `initial_message` first.

    On an unhandled exception, Textual prints a full traceback to stderr and exits the app
    rather than raising out of `App.run()` (see `App._handle_exception`), so that traceback is
    also captured to a `/tmp` crash log file — via `CrashLogTee` standing in for `App.
    error_console`'s output stream — before `_handle_repl_crash` reports the crash (and saves
    session state, where possible) once `run()` returns.
    """
    workspace_root = session.config.workspace.path if session is not None else Path.cwd()
    crash_tee = CrashLogTee(sys.stderr, crash_log_path(workspace_root))
    app = ReplApp(
        session=session,
        process_config=process_config,
        initial_message=initial_message,
        session_log_enabled=session_log_enabled,
        trust_manager=trust_manager,
        config_flag_path=config_flag_path,
    )
    app.error_console.file = crash_tee
    try:
        app.run()
        if app.return_code == 1:
            _handle_repl_crash(app, crash_tee)
    finally:
        # Belt-and-suspenders alongside `ReplApp.on_unmount`: make sure the liveness watchdog
        # can't fire during post-run crash handling / save once the event loop has stopped
        # snoozing it (see docs/specs/interrupt-and-liveness-watchdog.md).
        app._watchdog.stop()
        crash_tee.close()
