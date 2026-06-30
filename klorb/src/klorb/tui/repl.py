# © Copyright 2026 Aaron Kimball
"""A Textual-based interactive REPL: a scrolling prompt/response history with an input
box pinned to the bottom of the screen.
"""

from textual import work
from textual.app import App
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer
from textual.widgets import Input
from textual.widgets import Markdown
from textual.widgets import Static

from klorb.api_provider import ApiProvider
from klorb.openrouter import DEFAULT_MODEL
from klorb.openrouter import OpenRouterApiProvider

HISTORY_ID = "history"
PROMPT_INPUT_ID = "prompt-input"


class ReplApp(App[None]):
    """Interactive REPL: a scrolling history of prompts/responses, with a bottom input box."""

    CSS = """
    #history {
        height: 1fr;
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

    def __init__(self, provider: ApiProvider | None = None, model: str = DEFAULT_MODEL) -> None:
        super().__init__()
        self._provider = provider or OpenRouterApiProvider()
        self._model = model

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id=HISTORY_ID)
        yield Input(placeholder="Send a message...", id=PROMPT_INPUT_ID)
        yield Footer()

    def on_mount(self) -> None:
        """Focus the input box so the user can start typing immediately."""
        self.query_one(f"#{PROMPT_INPUT_ID}", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Echo the submitted prompt into the history and dispatch it to the model."""
        prompt_text = event.value.strip()
        if not prompt_text:
            return

        input_widget = event.input
        input_widget.value = ""
        input_widget.disabled = True

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(prompt_text, classes="prompt"))
        history.scroll_end(animate=False)

        self._send_prompt(prompt_text)

    @work(thread=True)
    def _send_prompt(self, prompt_text: str) -> None:
        """Send `prompt_text` to the model on a worker thread so the UI stays responsive."""
        try:
            response_text = self._provider.send_prompt(prompt_text, model=self._model)
        except Exception as exc:
            self.call_from_thread(self._show_error, str(exc))
        else:
            self.call_from_thread(self._show_response, response_text)

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


def run_repl(model: str = DEFAULT_MODEL) -> None:
    """Launch the interactive klorb REPL."""
    ReplApp(model=model).run()
