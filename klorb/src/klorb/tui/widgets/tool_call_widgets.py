# © Copyright 2026 Aaron Kimball
"""Tool-call widgets: the safety-limit confirmation modal, the running/finished tool-call
summary statics, and the session-naming "Getting ready..." notice, all mounted into the REPL
history."""

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from klorb.tui.formatting import ANIMATION_TICK_SECONDS, crawl_animation_text


class ToolCallLimitScreen(ModalScreen[bool]):
    """Modal asking whether to double a tool-call safety limit and keep going, shown when
    `Session` reports one has been reached (see `ReplApp._on_tool_call_limit_reached`).
    "Yes" or Enter (via the focused "Yes" button) confirms; "No" or Escape declines. Left/Right
    arrow keys move focus between the two buttons, same as Tab/Shift+Tab. Press `y` to accept or
    `n` to decline without moving focus.
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
        ("y", "accept", "Yes"),
        ("n", "decline", "No"),
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

    def action_accept(self) -> None:
        self.dismiss(True)

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
"""The literal text shown with a crawling bold-character animation (`crawl_animation_text`)
while a tool call executes."""


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
        self._timer = self.set_interval(ANIMATION_TICK_SECONDS, self._tick)

    def _make_running_text(self) -> Text:
        """Build a `Text` with the tool label in default style and ``Running...`` where one
        character is bright white at the current crawl position."""
        text = Text(self._label_text)
        text.append("\n")
        text.append_text(crawl_animation_text(_ANIMATED_RUNNING_TEXT, self._animation_pos))
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


_GETTING_READY_TEXT = "Getting ready..."
"""The literal text shown with a crawling bold-character animation (`crawl_animation_text`)
while the first-turn session-naming classifier call (`klorb.session_naming`) is in flight."""


class GettingReadyStatic(Static):
    """Animated notice mounted into history while the first-turn session-naming classifier
    call is in flight -- the naming-step analog of `RunningToolCallStatic`'s "Running..."
    animation, via the same `crawl_animation_text`. Constructed with `markup=False` for
    consistency with the other animated statics here, even though its text is a fixed literal
    rather than arbitrary content. `remove_self()` stops the timer before unmounting,
    mirroring `RunningToolCallStatic.finalize()`'s own timer teardown.
    """

    def __init__(self) -> None:
        super().__init__("", markup=False)
        self._animation_pos = 0
        self._timer: Any = None
        self.update(crawl_animation_text(_GETTING_READY_TEXT, 0))

    def on_mount(self) -> None:
        self._timer = self.set_interval(ANIMATION_TICK_SECONDS, self._tick)

    def _tick(self) -> None:
        self._animation_pos += 1
        self.update(crawl_animation_text(_GETTING_READY_TEXT, self._animation_pos))

    def remove_self(self) -> None:
        """Stop the crawl-animation timer, then unmount this widget from the history scroll."""
        if self._timer is not None:
            self._timer.stop()
        self.remove()
