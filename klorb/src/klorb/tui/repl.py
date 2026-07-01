# © Copyright 2026 Aaron Kimball
"""A Textual-based interactive REPL: a scrolling prompt/response history with an input
box pinned to the bottom of the screen.
"""

import logging

from textual import work
from textual.app import App
from textual.app import ComposeResult
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
from klorb.tui.model_commands import ModelCommandProvider

logger = logging.getLogger(__name__)

HISTORY_ID = "history"
PROMPT_INPUT_ID = "prompt-input"
CLEAR_COMMAND = "/clear"


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

    .prompt {
        color: $accent;
        text-style: bold;
        margin: 1 0 0 0;
    }

    .error {
        color: $error;
        margin: 0 0 1 0;
    }
    """

    BINDINGS = [("ctrl+c", "quit", "Quit"), ("ctrl+q", "quit", "Quit")]
    COMMANDS = App.COMMANDS | {ModelCommandProvider}

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
        yield Footer()

    def select_model(self, name: str) -> None:
        """Make `name` the active model used for subsequent prompts."""
        self._session.config.model = name
        self.sub_title = name
        self.notify(f"Model set to {name}.")

    def on_mount(self) -> None:
        """Label and focus the input box, then submit any initial message as the first turn."""
        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", Input)
        input_widget.border_title = "message"
        input_widget.focus()

        if self._initial_message:
            self._submit_prompt(self._initial_message)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Echo the submitted prompt into the history and dispatch it to the model, or
        handle a slash command (currently only `/clear`) synchronously.
        """
        prompt_text = event.value.strip()
        if not prompt_text:
            return

        event.input.value = ""
        if prompt_text == CLEAR_COMMAND:
            self._clear_session()
            return

        self._submit_prompt(prompt_text)

    def _clear_session(self) -> None:
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
        mounted on the first chunk and updated on each subsequent one.
        """
        response_widget: Markdown | None = None
        accumulated = ""

        def handle_chunk(delta_text: str) -> None:
            nonlocal accumulated, response_widget
            accumulated += delta_text
            if response_widget is None:
                response_widget = self.call_from_thread(self._mount_response_widget, accumulated)
            else:
                self.call_from_thread(response_widget.update, accumulated)

        try:
            response_text = self._session.send_turn(prompt_text, on_chunk=handle_chunk)
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
        """Scroll the history into view and hand focus back to the input box."""
        history.scroll_end(animate=False)
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
