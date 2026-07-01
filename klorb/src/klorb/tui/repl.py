# © Copyright 2026 Aaron Kimball
"""A Textual-based interactive REPL: a scrolling prompt/response history with an input
box pinned to the bottom of the screen.
"""

import logging

from rich.markup import escape
from textual import work
from textual.app import App
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import VerticalScroll
from textual.widgets import Footer
from textual.widgets import Header
from textual.widgets import Input
from textual.widgets import Markdown
from textual.widgets import Static

from klorb.logging_config import configure_logging
from klorb.logging_config import session_log_path
from klorb.session import Session
from klorb.session import SessionConfig
from klorb.session import ThinkingEffort
from klorb.tui.model_commands import ModelCommandProvider
from klorb.tui.session_commands import CLEAR_SESSION_COMMAND
from klorb.tui.session_commands import SessionCommandProvider
from klorb.tui.thinking_commands import ThinkingCommandProvider

logger = logging.getLogger(__name__)

HISTORY_ID = "history"
PROMPT_INPUT_ID = "prompt-input"
STATUS_BAR_ID = "status-bar"
THINKING_LABEL = "<Thinking>"

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


class ReplApp(App[None]):
    """Interactive REPL: a scrolling history of prompts/responses, with a bottom input box."""

    CSS = """
    #history {
        height: 1fr;
    }

    #prompt-input, #prompt-input:focus {
        border: none;
        border-top: solid $accent;
        height: 2;
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
    """

    BINDINGS = [("ctrl+c", "quit", "Quit"), ("ctrl+q", "quit", "Quit")]
    COMMANDS = App.COMMANDS | {ModelCommandProvider, SessionCommandProvider, ThinkingCommandProvider}

    def __init__(
        self,
        session: Session | None = None,
        initial_message: str | None = None,
        session_log_enabled: bool = True,
    ) -> None:
        super().__init__()
        self._session = session or Session(SessionConfig())
        self._initial_message = initial_message
        self._session_log_enabled = session_log_enabled
        self.title = "klorb"
        self.sub_title = self._session.config.model

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id=HISTORY_ID)
        yield Input(placeholder="Send a message...", id=PROMPT_INPUT_ID)
        with Horizontal(id="status-row"):
            yield Footer()
            yield Static(id=STATUS_BAR_ID)

    def select_model(self, name: str) -> None:
        """Make `name` the active model used for subsequent prompts."""
        self._session.config.model = name
        self.sub_title = name
        self._update_status_bar()
        self.notify(f"Model set to {name}.")

    def set_thinking_enabled(self, enabled: bool) -> None:
        """Enable or disable extended-thinking requests for subsequent prompts."""
        self._session.config.thinking_enabled = enabled
        self.notify(f"Thinking {'enabled' if enabled else 'disabled'}.")

    def set_thinking_effort(self, effort: ThinkingEffort) -> None:
        """Set the reasoning effort level used for subsequent prompts, when thinking is
        enabled and the active model supports it.
        """
        self._session.config.thinking_effort = effort
        self.notify(f"Thinking effort set to {effort}.")

    def on_mount(self) -> None:
        """Label and focus the input box, then submit any initial message as the first turn."""
        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", Input)
        input_widget.border_title = "message"
        input_widget.focus()
        self._update_status_bar()

        if self._initial_message:
            self._submit_prompt(self._initial_message)

    def _update_status_bar(self) -> None:
        """Refresh the status bar's running token tally against the active model's context window."""
        status_bar = self.query_one(f"#{STATUS_BAR_ID}", Static)
        used = format_token_count(self._session.total_tokens_used())
        limit = self._session.max_context_window()
        status_bar.update(used if limit is None else f"{used} / {format_token_count(limit)}")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Echo the submitted prompt into the history and dispatch it to the model, or
        handle a slash command (currently only `/clear`) synchronously.
        """
        prompt_text = event.value.strip()
        if not prompt_text:
            return

        event.input.value = ""
        if prompt_text == CLEAR_SESSION_COMMAND:
            self.clear_session()
            return

        self._submit_prompt(prompt_text)

    def clear_session(self) -> None:
        """Replace the active Session with a fresh one (same model, new id), reset the
        visible history, and roll over the per-session log file if session logging is
        enabled for this REPL invocation.
        """
        new_config = SessionConfig(
            model=self._session.config.model, interactive=self._session.config.interactive)
        self._session = Session(
            new_config, provider=self._session.provider, model_registry=self._session.model_registry)

        if self._session_log_enabled:
            log_path = session_log_path(self._session.id)
            configure_logging(repl_mode=True, log_path=log_path)
            logger.debug("Cleared session; now logging to %s", log_path)

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.remove_children()

        self.query_one(f"#{PROMPT_INPUT_ID}", Input).focus()
        self._update_status_bar()
        self.notify("Session cleared.")

    def _submit_prompt(self, prompt_text: str) -> None:
        """Echo `prompt_text` into the history, disable the input, and dispatch it."""
        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", Input)
        input_widget.disabled = True

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(prompt_text, classes="prompt"))
        history.scroll_end(animate=False)

        self._send_prompt(prompt_text)

    @work(thread=True)
    def _send_prompt(self, prompt_text: str) -> None:
        """Send `prompt_text` to the model on a worker thread so the UI stays responsive.

        Renders the response progressively as chunks stream in: a `Markdown` widget is
        mounted on the first chunk and updated on each subsequent one. Reasoning/thinking
        chunks, if the model sends any, are rendered the same way but behind a `<Thinking>`
        label and in italics, via a separate `Static` widget (not `Markdown`: reasoning text
        commonly spans multiple paragraphs, and Markdown's `*...*` emphasis syntax doesn't
        apply across blank-line-separated blocks, whereas Rich's `[italic]...[/italic]`
        console markup styles every line regardless).
        """
        response_widget: Markdown | None = None
        accumulated = ""
        thinking_widget: Static | None = None
        thinking_accumulated = ""

        def handle_chunk(delta_text: str) -> None:
            nonlocal accumulated, response_widget
            accumulated += delta_text
            if response_widget is None:
                response_widget = self.call_from_thread(self._mount_response_widget, accumulated)
            else:
                self.call_from_thread(response_widget.update, accumulated)

        def handle_thinking_chunk(delta_text: str) -> None:
            nonlocal thinking_accumulated, thinking_widget
            thinking_accumulated += delta_text
            if thinking_widget is None:
                thinking_widget = self.call_from_thread(self._mount_thinking_widget, thinking_accumulated)
            else:
                self.call_from_thread(thinking_widget.update, _italicized(thinking_accumulated))

        try:
            response_text = self._session.send_turn(
                prompt_text, on_chunk=handle_chunk, on_thinking_chunk=handle_thinking_chunk)
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
        widget = Markdown(initial_text)
        history.mount(widget)
        history.scroll_end(animate=False)
        return widget

    def _mount_thinking_widget(self, initial_text: str) -> Static:
        """Mount a left-justified `<Thinking>` label followed by an italicized `Static`
        widget for a streaming thinking block, and return that `Static` widget.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(THINKING_LABEL, classes="thinking-label"))
        widget = Static(_italicized(initial_text), classes="thinking-body")
        history.mount(widget)
        history.scroll_end(animate=False)
        return widget

    def _finalize_streamed_response(self, widget: Markdown, response_text: str) -> None:
        """Reconcile a streamed `Markdown` widget with the final response and finish the turn."""
        widget.update(response_text)
        self._finish_turn(self.query_one(f"#{HISTORY_ID}", VerticalScroll))

    def _show_response(self, response_text: str) -> None:
        """Append a model response to the history and re-enable the input box."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Markdown(response_text))
        self._finish_turn(history)

    def _show_error(self, message: str) -> None:
        """Append an error message to the history and re-enable the input box."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(f"Error: {message}", classes="error"))
        self._finish_turn(history)

    def _finish_turn(self, history: VerticalScroll) -> None:
        """Scroll the history into view, refresh the token tally, and hand focus back to the input box."""
        history.scroll_end(animate=False)
        self._update_status_bar()
        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", Input)
        input_widget.disabled = False
        input_widget.focus()


def run_repl(
    session: Session | None = None,
    initial_message: str | None = None,
    session_log_enabled: bool = True,
) -> None:
    """Launch the interactive klorb REPL, optionally submitting `initial_message` first."""
    ReplApp(session=session, initial_message=initial_message, session_log_enabled=session_log_enabled).run()
