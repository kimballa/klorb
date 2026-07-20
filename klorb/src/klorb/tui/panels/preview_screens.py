# © Copyright 2026 Aaron Kimball
"""Click-to-expand overlays for a diff/read preview in the history scroll (see
`klorb.tui.mixins.rendering.RenderingMixin`'s `RenderedToolCall.on_click`) — full-screen,
read-only, scrollable views modeled directly on `ExpandedCommandScreen`
(`klorb.tui.panels.permission_ask_panel`): a `ModalScreen` pushed via `self.push_screen(...)`,
dismissed with Escape, that doesn't disturb whatever is mounted underneath (a running turn, a
`PermissionAskPanel`, ...) -- it's a screen-stack push, not a synchronous blocking call, so the
agent keeps running and any interaction panel that needs to appear still does, in its usual
place, and simply waits until this overlay is dismissed.
"""

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
"""Shared body layout for `DiffDetailScreen`/`ReadDetailScreen`: `#preview-detail-label` sits
outside `#preview-detail-scroll` (a plain, non-scrolling `Static`, `height: auto`) so it stays
pinned at the top of the overlay as a sticky header -- only `#preview-detail-scroll`'s own content
scrolls beneath it, rather than the label scrolling away along with the rest of a long diff/file
the way a single shared `VerticalScroll` would."""


class DiffDetailScreen(ModalScreen[None]):
    """Full-screen view of one edit/create call's complete diff (`RenderedToolCall.detail_content`
    -- the same already-built `Content` Ctrl+O's detail view shows, just here full-screen and
    scrollable rather than capped to the height of one history entry)."""

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
    """Full-screen view of a `Read*` call's whole subject (built lazily, at click time, by
    `ReadPreview.open_full()` -- see `klorb.tools.tool.ReadPreview`), scrolled so
    `scroll_to_line` starts at the top of the viewport, matching the range the call actually
    read."""

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
