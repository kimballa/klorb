# © Copyright 2026 Aaron Kimball
"""A Textual-based interactive REPL: a scrolling prompt/response history with an input
box pinned to the bottom of the screen.
"""

import logging
import threading

from rich.markup import escape
from textual import events
from textual import work
from textual.app import App
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.containers import VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button
from textual.widgets import Footer
from textual.widgets import Header
from textual.widgets import Markdown
from textual.widgets import Static
from textual.widgets import TextArea

from klorb.api_provider import ResponseAborted
from klorb.logging_config import configure_logging
from klorb.logging_config import session_log_path
from klorb.permissions.grant import compute_grant_paths
from klorb.process_config import ProcessConfig
from klorb.session import PermissionAskContext
from klorb.session import PermissionDecision
from klorb.session import Session
from klorb.session import ThinkingEffort
from klorb.session import ToolCallEvent
from klorb.session import TurnEventHandlers
from klorb.tools.registry import ToolRegistry
from klorb.tools.tool import default_tool_call_detail
from klorb.tools.tool import default_tool_call_summary
from klorb.tui.model_commands import ModelCommandProvider
from klorb.tui.permission_ask_screen import PermissionAskScreen
from klorb.tui.session_commands import CLEAR_SESSION_COMMAND
from klorb.tui.session_commands import SessionCommandProvider
from klorb.tui.shell import ShellCommandCancelled
from klorb.tui.shell import ShellCommandTimedOut
from klorb.tui.shell import UserShellCommand
from klorb.tui.thinking_commands import ThinkingCommandProvider

logger = logging.getLogger(__name__)

HISTORY_ID = "history"
PROMPT_INPUT_ID = "prompt-input"
STATUS_BAR_ID = "status-bar"
THINKING_LABEL = "<Thinking>"
TOOL_USE_LABEL = "<Tool use>"

_SI_SUFFIXES = ("", "k", "M", "B")


def _round_to_2_sig_figs(value: float) -> float:
    """Round `value` (>= 1) to 2 significant figures, e.g. `1.43` -> `1.4`, `423` -> `420`."""
    if value < 10:
        return round(value, 1)
    if value < 100:
        return float(round(value))
    return float(round(value / 10) * 10)


def format_token_count(count: int) -> str:
    """Render `count` using SI-style suffixes at 2 significant figures once >= 1000, e.g.
    `1400` -> `"1.4k"`, `23000` -> `"23k"`, `423000` -> `"420k"`. Counts under 1000 are
    shown as a literal integer.
    """
    if count < 1000:
        return str(count)

    magnitude = 0
    value = float(count)
    while value >= 1000 and magnitude < len(_SI_SUFFIXES) - 1:
        value /= 1000
        magnitude += 1

    value = _round_to_2_sig_figs(value)
    if value >= 1000 and magnitude < len(_SI_SUFFIXES) - 1:
        value /= 1000
        magnitude += 1

    suffix = _SI_SUFFIXES[magnitude]
    if value == int(value):
        return f"{int(value)}{suffix}"
    return f"{value}{suffix}"


def _italicized(text: str) -> str:
    """Wrap `text` in Rich console markup for italics, escaping any literal `[`/`]` in
    `text` first so it can't be misread as markup by the `Static` widget it's rendered in.
    """
    return f"[italic]{escape(text)}[/italic]"


def _pinned_to_bottom(history: VerticalScroll) -> bool:
    """Whether `history`'s viewport is currently showing its bottom edge, i.e. whether the
    user hasn't scrolled away from the latest content.

    Only `ReplApp._on_history_scroll_changed` should call this: it's accurate exactly when
    `history`'s `scroll_y` has just changed (mounting a widget doesn't itself update
    `max_scroll_y` until the next layout refresh, so calling this at an arbitrary other time
    can read a stale comparison). Everywhere else that needs to know whether to follow new
    content to the bottom reads the cached `ReplApp._history_pinned_to_bottom` instead.
    """
    return history.is_vertical_scroll_end


class PromptInput(TextArea):
    """A multi-line prompt box that soft-wraps long input and grows with it.

    Enter submits the current text, mirroring the single-line `Input` widget this replaces.
    Ctrl+Enter inserts a literal newline instead. Shift+Enter and Alt+Enter aren't usable for
    this: Shift+Enter is indistinguishable from plain Enter on terminals without kitty
    keyboard protocol support (confirmed by testing: both report as plain `"enter"`), and
    Alt+Enter is claimed by Windows/many terminal emulators for toggling fullscreen before it
    ever reaches the app. Ctrl+Enter itself commonly arrives as `"ctrl+j"` rather than
    `"ctrl+enter"`, since terminals typically send the same linefeed control byte for both
    Ctrl+Enter and Ctrl+J; both spellings are handled here.
    """

    NEWLINE_KEYS = ("ctrl+j", "ctrl+enter")

    class Submitted(Message):
        """Posted when the user presses Enter to submit the current text."""

        def __init__(self, prompt_input: "PromptInput", value: str) -> None:
            self.prompt_input = prompt_input
            self.value = value
            super().__init__()

        @property
        def control(self) -> "PromptInput":
            return self.prompt_input

    async def _on_key(self, event: events.Key) -> None:
        """Submit on Enter; insert a newline on Ctrl+Enter; defer everything else (typing,
        navigation, selection) to `TextArea`'s own handling.
        """
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self, self.text))
            return
        if event.key in self.NEWLINE_KEYS:
            event.stop()
            event.prevent_default()
            self.replace("\n", *self.selection)
            return
        await super()._on_key(event)


class ToolCallLimitScreen(ModalScreen[bool]):
    """Modal asking whether to double a tool-call safety limit and keep going, shown when
    `Session` reports one has been reached (see `ReplApp._on_tool_call_limit_reached`).
    "Yes" or Enter (via the focused "Yes" button) confirms; "No" or Escape declines.
    """

    CSS = """
    ToolCallLimitScreen {
        align: center middle;
    }

    ToolCallLimitScreen Vertical {
        width: auto;
        max-width: 60;
        height: auto;
        border: round $accent;
        padding: 1 2;
    }

    #tool-call-limit-message {
        margin: 0 0 1 0;
    }

    #tool-call-limit-buttons {
        align: center middle;
        height: auto;
    }

    #tool-call-limit-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [("escape", "decline", "No")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self._message, id="tool-call-limit-message"),
            Horizontal(
                Button("Yes", id="tool-call-limit-yes", variant="primary"),
                Button("No", id="tool-call-limit-no"),
                id="tool-call-limit-buttons",
            ),
        )

    def on_mount(self) -> None:
        self.query_one("#tool-call-limit-yes", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "tool-call-limit-yes")

    def action_decline(self) -> None:
        self.dismiss(False)


class ToolCallStatic(Static):
    """One finished tool call in the history, shown as either its one-line summary or its
    fuller detail view — toggled globally, across every `ToolCallStatic` at once, by Ctrl+O
    (see `ReplApp.action_toggle_tool_call_detail`).
    """

    def __init__(self, summary_text: str, detail_text: str) -> None:
        super().__init__(escape(summary_text), classes="tool-call")
        self._summary_text = summary_text
        self._detail_text = detail_text

    def set_detail_shown(self, show_detail: bool) -> None:
        """Update the rendered content to the detail view if `show_detail`, else the summary."""
        self.update(escape(self._detail_text if show_detail else self._summary_text))


class ReplApp(App[None]):
    """Interactive REPL: a scrolling history of prompts/responses, with a bottom input box."""

    CSS = """
    #history {
        height: 1fr;
    }

    #prompt-input, #prompt-input:focus {
        border: none;
        border-top: solid $accent;
        height: auto;
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
        background: $footer-description-background;
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
    }

    .tool-call-label {
        color: $text-muted;
        margin: 1 0 0 0;
    }

    .tool-call {
        color: $text-muted;
        padding: 0 2;
    }

    .response {
        margin: 1 0 0 0;
    }

    .interrupted {
        color: $text-muted;
        margin: 1 0 0 0;
    }
    """

    BINDINGS = [
        ("ctrl+c", "interrupt", "Quit"),
        ("ctrl+q", "quit", "Quit"),
        ("escape", "abort_response", "Abort"),
        ("ctrl+o", "toggle_tool_call_detail", "Detail"),
    ]
    COMMANDS = App.COMMANDS | {ModelCommandProvider, SessionCommandProvider, ThinkingCommandProvider}

    def __init__(
        self,
        session: Session | None = None,
        process_config: ProcessConfig | None = None,
        initial_message: str | None = None,
        session_log_enabled: bool = True,
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
        self._cancel_event: threading.Event | None = None
        self._shell_cancel_event: threading.Event | None = None
        self._tool_call_widgets: list[ToolCallStatic] = []
        self._tool_call_detail_shown: bool = False
        self._history_pinned_to_bottom: bool = True
        self.title = "klorb"
        self.sub_title = self._session.config.model

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id=HISTORY_ID)
        yield PromptInput(placeholder="Send a message...", id=PROMPT_INPUT_ID)
        with Horizontal(id="status-row"):
            yield Footer()
            yield Static(id=STATUS_BAR_ID)

    def select_model(self, name: str) -> None:
        """Make `name` the active model used for subsequent prompts, and the default model
        for any future session started in this process.
        """
        self._session.config.model = name
        self._process_config.session.model = name
        self.sub_title = name
        self._update_status_bar()
        self.notify(f"Model set to {name}.")

    def get_thinking_effort(self) -> ThinkingEffort:
        """Return the reasoning effort level currently configured for subsequent prompts."""
        return self._session.config.thinking_effort

    def get_thinking_enabled(self) -> bool:
        """Return whether extended-thinking requests are currently enabled."""
        return self._session.config.thinking_enabled

    def set_thinking_enabled(self, enabled: bool) -> None:
        """Enable or disable extended-thinking requests for subsequent prompts, and for any
        future session started in this process.
        """
        self._session.config.thinking_enabled = enabled
        self._process_config.session.thinking_enabled = enabled
        self.notify(f"Thinking {'enabled' if enabled else 'disabled'}.")

    def set_thinking_effort(self, effort: ThinkingEffort) -> None:
        """Set the reasoning effort level used for subsequent prompts, when thinking is
        enabled and the active model supports it. Also becomes the default effort level for
        any future session started in this process.
        """
        self._session.config.thinking_effort = effort
        self._process_config.session.thinking_effort = effort
        self.notify(f"Thinking effort set to {effort}.")

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Hide the `abort_response` binding from the footer unless a response is currently
        streaming in, since there's nothing for Escape to abort otherwise.
        """
        if action == "abort_response":
            return self._cancel_event is not None
        return True

    def action_abort_response(self) -> None:
        """Signal the in-flight turn's worker thread to stop consuming the response stream."""
        if self._cancel_event is not None:
            self._cancel_event.set()

    def action_interrupt(self) -> None:
        """Ctrl+C: if a `!`-prefixed shell command is currently running, terminate it instead
        of quitting — mirroring a terminal's own Ctrl+C, which interrupts the foreground job
        rather than closing the shell. With no shell command in flight, falls through to the
        ordinary quit behavior (matching `action_quit`, which Ctrl+C used to be bound to
        directly).
        """
        if self._shell_cancel_event is not None:
            self._shell_cancel_event.set()
        else:
            self.exit()

    def action_toggle_tool_call_detail(self) -> None:
        """Ctrl+O: flip every `ToolCallStatic` currently in the history — from any turn, not
        just the latest — between its one-line summary and its fuller detail view, all at once.

        Also updates the footer's own label for this binding — `"Detail"` while summaries are
        shown (offering to reveal detail), `"Hide"` once detail is shown (offering to go back)
        — since a binding's description is otherwise fixed at `BINDINGS` class-definition time.
        `self._bindings` is this `ReplApp` instance's own mutable copy of the merged class-level
        `BINDINGS` (see `DOMNode.__init__`), so replacing its `"ctrl+o"` entry only affects this
        instance; `refresh_bindings()` is Textual's documented hook for prompting `Footer` to
        redraw after such a change.
        """
        self._tool_call_detail_shown = not self._tool_call_detail_shown
        for widget in self._tool_call_widgets:
            widget.set_detail_shown(self._tool_call_detail_shown)
        label = "Hide" if self._tool_call_detail_shown else "Detail"
        self._bindings.key_to_bindings["ctrl+o"] = [
            Binding("ctrl+o", "toggle_tool_call_detail", label)]
        self.refresh_bindings()

    def on_mount(self) -> None:
        """Label and focus the input box, cap its growth at the configured max height, watch
        the history's scroll position (see `_on_history_scroll_changed`), then submit any
        initial message as the first turn.
        """
        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        input_widget.border_title = "message"
        input_widget.styles.max_height = self._process_config.prompt_input_max_lines + 1
        input_widget.focus()
        self._update_status_bar()

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        self.watch(history, "scroll_y", self._on_history_scroll_changed, init=False)

        if self._initial_message:
            self._submit_prompt(self._initial_message)

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
        """Refresh the status bar's running token tally against the active model's context window."""
        status_bar = self.query_one(f"#{STATUS_BAR_ID}", Static)
        used = format_token_count(self._session.total_tokens_used())
        limit = self._session.max_context_window()
        status_bar.update(used if limit is None else f"{used} / {format_token_count(limit)}")

    def on_prompt_input_submitted(self, event: PromptInput.Submitted) -> None:
        """Echo the submitted prompt into the history and dispatch it to the model, or
        handle a slash command (currently only `/clear`) or a `!`-prefixed shell command
        synchronously.
        """
        prompt_text = event.value.strip()
        if not prompt_text:
            return

        event.prompt_input.text = ""
        if prompt_text == CLEAR_SESSION_COMMAND:
            self.clear_session()
            return

        if prompt_text in [":q", "/quit", "/exit"]:
            self.exit()
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
        start a second one while the first is still running.
        """
        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        input_widget.disabled = True

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(f"!{command}", classes="prompt"))
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
                        self._update_shell_output_widget, output_widget, escape(accumulated))

        error_message: str | None = None
        try:
            _, _, rc = UserShellCommand(command, shell_path=self._process_config.shell_command).run(
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

    def _mount_shell_output_widget(self, initial_text: str) -> Static:
        """Mount a new `Static` widget for a streaming shell command's output and return it.

        Uses `Static` rather than the `Markdown` widget `_mount_response_widget` mounts for a
        streaming model reply: shell output is plain text, not markdown, and `Markdown`'s
        CommonMark rendering collapses a single newline within a paragraph into a soft line
        break (a space), which mangles multi-line command output. `Static` renders text
        verbatim, preserving every newline. `initial_text` is escaped so literal `[`/`]` in the
        output (e.g. `[INFO]` log tags) can't be misread as Rich console markup.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget = Static(escape(initial_text))
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        return widget

    def _update_shell_output_widget(self, widget: Static, text: str) -> None:
        """Update a streaming shell command's output `Static` widget with the latest
        accumulated `text` (already escaped), following the view to the bottom only if it was
        already pinned there before this growth (see `_history_pinned_to_bottom`) — so a user
        who's scrolled up to reread earlier output isn't yanked back down by every incoming
        chunk.
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
        """Replace the active Session with a fresh one (new id, config reset to the current
        process-level template), reset the visible history, and roll over the per-session
        log file if session logging is enabled for this REPL invocation.
        """
        new_config = self._process_config.session.model_copy()
        self._session = Session(
            new_config,
            provider=self._session.provider,
            model_registry=self._session.model_registry,
            process_config=self._process_config,
            tool_registry=ToolRegistry(self._process_config, new_config),
        )

        if self._session_log_enabled:
            log_path = session_log_path(self._session.id)
            configure_logging(repl_mode=True, log_path=log_path)
            logger.debug("Cleared session; now logging to %s", log_path)

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.remove_children()

        self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput).focus()
        self._update_status_bar()
        self.notify("Session cleared.")

    def _submit_prompt(self, prompt_text: str) -> None:
        """Echo `prompt_text` into the history, disable the input, and dispatch it."""
        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        input_widget.disabled = True

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        prompt_widget = Static(prompt_text, classes="prompt")
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
        console markup styles every line regardless).

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
                    self._update_thinking_widget, thinking_widget, _italicized(thinking_accumulated))

        def handle_tool_call(event: ToolCallEvent) -> None:
            nonlocal round_index
            summary_text, detail_text = self._render_tool_call(event)
            self.call_from_thread(self._mount_tool_call_widget, summary_text, detail_text)
            round_index += 1

        try:
            response_text = self._session.send_turn(prompt_text, TurnEventHandlers(
                on_chunk=handle_chunk, on_thinking_chunk=handle_thinking_chunk,
                cancel_event=cancel_event, on_tool_call_limit_reached=self._on_tool_call_limit_reached,
                on_permission_ask=self._on_permission_ask, on_tool_call=handle_tool_call))
        except ResponseAborted:
            self.call_from_thread(
                self._handle_aborted_response, response_widget, accumulated,
                thinking_widget, thinking_accumulated)
        except Exception as exc:
            self.call_from_thread(self._show_error, str(exc))
        else:
            if response_widget is not None:
                self.call_from_thread(self._finalize_streamed_response, response_widget, response_text)
            else:
                self.call_from_thread(self._show_response, response_text)

    def _mount_response_widget(self, initial_text: str) -> Markdown:
        """Mount a new `Markdown` widget for a streaming response and return it."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget = Markdown(initial_text, classes="response")
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        return widget

    def _update_response_widget(self, widget: Markdown, text: str) -> None:
        """Update a streaming response `Markdown` widget with the latest accumulated `text`,
        following the view to the bottom only if it was already pinned there before this change
        (see `_history_pinned_to_bottom`), so a user who's scrolled up to reread earlier output
        isn't yanked back down by every incoming chunk. `widget` is always still the last thing
        mounted in the history at this point: `_send_prompt` starts a fresh widget rather than
        reusing this one once a round boundary (a tool call) has passed, so there's nothing
        mounted after it left to get stuck behind.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(text)
        self._scroll_if_pinned(history, was_pinned)

    def _mount_thinking_widget(self, initial_text: str) -> tuple[Static, Static]:
        """Mount a left-justified `<Thinking>` label followed by an italicized `Static`
        widget for a streaming thinking block, and return `(body_widget, label_widget)`.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        label_widget = Static(THINKING_LABEL, classes="thinking-label")
        history.mount(label_widget)
        widget = Static(_italicized(initial_text), classes="thinking-body")
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        return widget, label_widget

    def _update_thinking_widget(self, widget: Static, italicized_text: str) -> None:
        """Update a streaming `<Thinking>` `Static` widget with `italicized_text` (already run
        through `_italicized`), following the view to the bottom only if it was already pinned
        there before this change (see `_update_response_widget`) — the label mounted alongside
        `widget` never changes after being set, so only the body needs updating here.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(italicized_text)
        self._scroll_if_pinned(history, was_pinned)

    def _render_tool_call(self, event: ToolCallEvent) -> tuple[str, str]:
        """Render `event` as `(summary_text, detail_text)` via its tool's `summary()`/
        `detail_view()` — instantiating a fresh `Tool` purely to call these pure methods
        (`ToolRegistry.instantiate_tool()` is already a cheap, no-shared-state operation; see
        docs/adrs/tool-registry-instantiates-a-fresh-tool-per-call.md) — or via the shared
        default formatters if `event.name` isn't a registered tool (e.g. a hallucinated tool
        name, which `Session._run_tool_calls` already turned into `event.error`).
        """
        registry = self._session.tool_registry
        try:
            if registry is None:
                raise KeyError(event.name)
            tool = registry.instantiate_tool(event.name)
        except KeyError:
            return (default_tool_call_summary(event.name, event.args, event.error),
                    default_tool_call_detail(event.name, event.args, event.result, event.error))
        return (tool.summary(event.args, event.result, event.error),
                tool.detail_view(event.args, event.result, event.error))

    def _mount_tool_call_widget(self, summary_text: str, detail_text: str) -> tuple[ToolCallStatic, Static]:
        """Mount a left-justified `<Tool use>` label (styled via the `.tool-call-label` CSS
        class, matching `<Thinking>`'s label/body split in `_mount_thinking_widget`) followed
        by a `ToolCallStatic` for one finished tool call, and return `(widget, label_widget)`.
        Applies the current global detail-shown state (see `action_toggle_tool_call_detail`)
        so a call rendered while detail view is already active shows detail immediately rather
        than a summary the user would have to toggle past.
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
        return widget, label_widget

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

    async def _confirm_permission_ask(self, ask_ctx: PermissionAskContext) -> PermissionDecision:
        """Show `PermissionAskScreen` for `ask_ctx` and wait for the user's choice.

        Must be run on the app's own event loop, since it awaits the screen's dismissal —
        `_on_permission_ask` is what the worker thread actually calls, via `call_from_thread`,
        to get here. `compute_grant_paths()` is recomputed here (rather than threaded in from
        wherever `ask_ctx` was built) purely so the modal can show the directory a persistent
        grant would actually cover; it's pure and read-only, so calling it again inside
        `_on_permission_ask`/`apply_permission_grant` afterwards is safe and cheap.
        """
        granted_paths = compute_grant_paths(
            self._session.config.read_dirs, self._session.config.write_dirs,
            self._session.config.workspace_root, ask_ctx.path, ask_ctx.is_write)
        return await self.push_screen_wait(PermissionAskScreen(ask_ctx, granted_paths))

    def _on_permission_ask(self, ask_ctx: PermissionAskContext) -> PermissionDecision:
        """`Session`'s `on_permission_ask` callback: block the worker thread running
        `Session.send_turn()` until the user answers `PermissionAskScreen`, then return that
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
        """Append an error message to the history and re-enable the input box."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        history.mount(Static(f"Error: {message}", classes="error"))
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
            thinking_widget.update(_italicized(f"{thinking_text}\n\n(interrupted)"))
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
        time.
        """
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()
        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        input_widget.disabled = False
        input_widget.focus()
        self._cancel_event = None
        self._shell_cancel_event = None
        self.refresh_bindings()


def run_repl(
    session: Session | None = None,
    process_config: ProcessConfig | None = None,
    initial_message: str | None = None,
    session_log_enabled: bool = True,
) -> None:
    """Launch the interactive klorb REPL, optionally submitting `initial_message` first."""
    ReplApp(
        session=session,
        process_config=process_config,
        initial_message=initial_message,
        session_log_enabled=session_log_enabled,
    ).run()
