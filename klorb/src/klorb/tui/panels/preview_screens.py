# © Copyright 2026 Aaron Kimball
"""Click-to-expand overlays for a diff/read preview in the history scroll."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Static

_BODY_CSS = """
    #preview-detail-body {
        width: 90%;
        height: 90%;
        border: round $accent;
        padding: 1 2;
    }

    #preview-detail-label {
        text-style: bold;
        height: auto;
        margin: 0 0 1 0;
    }

    #preview-detail-scroll {
        height: 1fr;
    }
    """
"""Shared body layout for `DiffDetailScreen`/`ReadDetailScreen`: `#preview-detail-label` stays
pinned at the top as a sticky header while `#preview-detail-scroll` scrolls beneath it."""


class DiffDetailScreen(ModalScreen[None]):
    """Full-screen view of one edit/create call's complete diff."""

    CSS = "DiffDetailScreen { align: center middle; }\n" + _BODY_CSS

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, label: str, content: Content) -> None:
        super().__init__()
        self._label = label
        self._content = content

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self._label, id="preview-detail-label", markup=False),
            VerticalScroll(
                Static(self._content, id="preview-detail-content", markup=False),
                id="preview-detail-scroll"),
            id="preview-detail-body",
        )

    def action_close(self) -> None:
        self.dismiss(None)


class ReadDetailScreen(ModalScreen[None]):
    """Full-screen view of a `Read*` call's whole subject, scrolled so `scroll_to_line`
    starts at the top of the viewport."""

    CSS = "ReadDetailScreen { align: center middle; }\n" + _BODY_CSS

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, label: str, content: Content, *, scroll_to_line: int) -> None:
        super().__init__()
        self._label = label
        self._content = content
        self._scroll_to_line = scroll_to_line

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self._label, id="preview-detail-label", markup=False),
            VerticalScroll(
                Static(self._content, id="preview-detail-content", markup=False),
                id="preview-detail-scroll"),
            id="preview-detail-body",
        )

    def on_mount(self) -> None:
        self.query_one("#preview-detail-scroll", VerticalScroll).scroll_to(
            y=max(0, self._scroll_to_line - 1), animate=False)

    def action_close(self) -> None:
        self.dismiss(None)
