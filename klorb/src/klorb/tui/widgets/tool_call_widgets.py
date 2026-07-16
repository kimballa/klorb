# © Copyright 2026 Aaron Kimball
"""Tool-call widgets: the safety-limit confirmation modal and the running/finished
tool-call summary statics mounted into the REPL history."""

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ToolCallLimitScreen(ModalScreen[bool]):
    """Modal asking whether to double a tool-call safety limit and keep going, shown when
    `Session` reports one has been reached (see `ReplApp._on_tool_call_limit_reached`).
    "Yes" or Enter (via the focused "Yes" button) confirms; "No" or Escape declines. Left/Right
    arrow keys move focus between the two buttons, same as Tab/Shift+Tab.
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

    BINDINGS = [
        ("escape", "decline", "No"),
        Binding("left", "app.focus_previous", "Focus Previous", show=False),
        Binding("right", "app.focus_next", "Focus Next", show=False),
    ]

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
    (see `ReplApp.action_toggle_tool_call_detail`). Constructed with `markup=False`: summary
    and detail text are arbitrary tool output (JSON, file paths, shell output, ...) that must
    render verbatim, not be parsed as Textual console markup — an unescaped `[` in that text
    can otherwise be misread as the start of a markup tag and raise `MarkupError`.
    """

    def __init__(self, summary_text: str, detail_text: str) -> None:
        super().__init__(summary_text, classes="tool-call", markup=False)
        self._summary_text = summary_text
        self._detail_text = detail_text

    def set_detail_shown(self, show_detail: bool) -> None:
        """Update the rendered content to the detail view if `show_detail`, else the summary."""
        self.update(self._detail_text if show_detail else self._summary_text)


_ANIMATED_RUNNING_TEXT = "Running..."
"""The literal text shown with a crawling bold-character animation while a tool call executes."""
_ANIMATION_TICK_SECONDS = 0.24
"""Interval between animation frames (seconds). At 0.24s per frame and 9 characters in
`_ANIMATED_RUNNING_TEXT`, a full crawl cycle takes just over two seconds -- slow enough to
feel calm without being distracting."""


class RunningToolCallStatic(ToolCallStatic):
    """A tool call widget shown while the tool is still executing: displays the tool's
    pre-execution summary (e.g. ``Bash: <intent>\\n$ <command>``) plus a crawling bright-white
    animation on the word "Running..." so the user knows the system hasn't frozen.

    Inherits from `ToolCallStatic` so ``history.query(ToolCallStatic)`` finds it in the DOM
    -- the Ctrl+O detail toggle and any other `ToolCallStatic`-aware query work unchanged.
    Once the tool finishes, `finalize()` replaces the animated content with the final
    summary/detail text and stops the timer. Constructed with ``markup=False``: the tool
    label is arbitrary and must render verbatim, so per-character styling is applied via
    `Rich.text.Text` spans rather than console markup.
    """

    def __init__(self, label_text: str) -> None:
        # Initialize parent with empty placeholders; real content comes from the running
        # animation and later finalize().
        super().__init__("", "")
        self._label_text = label_text
        self._finalized: bool = False
        self._detail_shown: bool = False
        self._animation_pos: int = 0
        self._timer: Any = None
        # Override the parent's initial content with the running animation.
        self.update(self._make_running_text())

    def on_mount(self) -> None:
        self._timer = self.set_interval(_ANIMATION_TICK_SECONDS, self._tick)

    def _make_running_text(self) -> Text:
        """Build a `Text` with the tool label in default style and ``Running...`` where one
        character is bright white at the current crawl position."""
        text = Text(self._label_text)
        running = _ANIMATED_RUNNING_TEXT
        pos = self._animation_pos % len(running)
        text.append("\n")
        for i, ch in enumerate(running):
            if i == pos:
                text.append(ch, style="bright_white")
            else:
                text.append(ch)
        return text

    def _tick(self) -> None:
        """Advance the crawl animation by one position."""
        if self._finalized:
            return
        self._animation_pos += 1
        self.update(self._make_running_text())

    def finalize(self, summary_text: str, detail_text: str) -> None:
        """Stop the animation and replace the content with the final summary/detail text.
        After this call, the widget behaves identically to a `ToolCallStatic` for the
        Ctrl+O detail toggle."""
        self._finalized = True
        if self._timer is not None:
            self._timer.stop()
        self._summary_text = summary_text
        self._detail_text = detail_text
        self._apply_content()

    def _apply_content(self) -> None:
        """Set the widget's rendered content to either the detail or summary text."""
        content = self._detail_text if self._detail_shown else self._summary_text
        self.update(content)

    def set_detail_shown(self, show_detail: bool) -> None:
        """Update the rendered content to the detail view if `show_detail`, else the summary.
        While still running (not yet finalized), this is a no-op -- there's no detail view
        to show until the tool call completes."""
        self._detail_shown = show_detail
        if self._finalized:
            self._apply_content()
