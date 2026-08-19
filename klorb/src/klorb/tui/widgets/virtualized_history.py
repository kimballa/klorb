# © Copyright 2026 Aaron Kimball
"""`VirtualizedHistoryContainer`: bounds the number of permanently-mounted widgets in a
history `VerticalScroll` by collapsing older, settled, off-screen content into a single
placeholder widget per chunk, re-expandable on demand."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from textual.containers import VerticalScroll
from textual.dom import NoScreen
from textual.widget import Widget
from textual.widgets import Static

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE_MESSAGES = 25
"""How many `session.messages` entries a settled region accumulates before it becomes one
collapsible chunk."""

ESTIMATED_LINES_PER_SEEDED_MESSAGE = 6
"""Rough average rendered lines per message, so a seeded prefix's placeholder height roughly
reflects how much history it's replacing instead of always reading as a single line."""


class HistoryPlaceholder(Static):
    """Stands in for a collapsed chunk's real widgets: a single line reporting how many
    messages it hides, sized to approximate the space that content actually occupies."""

    def __init__(
        self, message_count: int, on_expand: Callable[[], Awaitable[None]],
        *, height: int | None = None,
    ) -> None:
        plural = "" if message_count == 1 else "s"
        super().__init__(
            f"— {message_count} earlier message{plural}, press ctrl+e to expand —",
            classes="history-placeholder", markup=False)
        self._on_expand = on_expand
        if height is not None and height > 1:
            self.styles.height = height

    async def on_click(self) -> None:
        await self._on_expand()


@dataclass
class _Chunk:
    """One contiguous range of `session.messages`, currently represented in the container
    either by its real mounted `widgets` (`placeholder is None`) or by a single
    `HistoryPlaceholder` standing in for them."""

    start_index: int
    end_index: int
    widgets: list[Widget] = field(default_factory=list)
    placeholder: HistoryPlaceholder | None = None
    expanding: bool = False
    """Set for the duration of an in-flight `_expand()` call, so a concurrent trigger doesn't
    start a duplicate expand of the same chunk."""
    collapsing: bool = False
    """Set for the duration of an in-flight `_collapse()` call, so a concurrent trigger doesn't
    start a duplicate collapse of the same chunk."""

    @property
    def message_count(self) -> int:
        return self.end_index - self.start_index


class VirtualizedHistoryContainer:
    """Keeps `container`'s live widget count bounded by grouping settled content into
    `chunk_size`-message chunks and collapsing an off-screen chunk to one placeholder widget.
    A chunk only starts accumulating once `close_trailing_region()` or
    `register_settled_range()` reports its message range, so a turn still streaming into a
    live widget is never collapsed out from under that update."""

    def __init__(
        self, container: VerticalScroll, get_message_count: Callable[[], int],
        render_range: Callable[[int, int], list[Widget]],
        call_after_refresh: Callable[[Callable[[], object]], object],
        chunk_size: int = DEFAULT_CHUNK_SIZE_MESSAGES,
    ) -> None:
        self._container = container
        self._get_message_count = get_message_count
        self._render_range = render_range
        # A chunk just sealed by `_accumulate` may include a widget mounted this same call
        # stack, whose `virtual_region` Textual hasn't laid out yet. `call_after_refresh`
        # defers the resulting visibility check until layout catches up, so it doesn't read
        # a stale/zero region.
        self._call_after_refresh = call_after_refresh
        self._chunk_size = chunk_size
        self._sealed: list[_Chunk] = []
        self._pending: _Chunk | None = None
        self._open_start_index: int | None = None
        self._open_widget_index: int | None = None

    def begin_trailing_region(self) -> None:
        """Mark the start of a new in-flight turn: widgets mounted from here on stay outside
        every chunk until the matching `close_trailing_region()`. A no-op if a trailing region
        is already open."""
        if self._open_start_index is not None:
            return
        self._open_start_index = self._get_message_count()
        self._open_widget_index = len(self._container.children)

    def close_trailing_region(self) -> None:
        """Settle the in-flight turn opened by `begin_trailing_region()`: the messages and
        widgets it produced become eligible for chunking. A no-op if no region is open."""
        if self._open_start_index is None:
            return
        start = self._open_start_index
        widget_index = self._open_widget_index
        self._open_start_index = None
        self._open_widget_index = None
        assert widget_index is not None
        end = self._get_message_count()
        widgets = list(self._container.children)[widget_index:]
        self._accumulate(start, end, widgets)

    def register_settled_range(self, start: int, end: int, widgets: list[Widget]) -> None:
        """Register `widgets` (already mounted, rendering `session.messages[start:end]`) as
        immediately eligible for chunking. A no-op if `end <= start`."""
        if end <= start:
            return
        self._accumulate(start, end, widgets)

    async def seed_collapsed_prefix(self, total_messages: int, trailing_window: int) -> int:
        """Register everything before the trailing `trailing_window` messages as a single
        collapsed placeholder, mounted immediately with no real widgets ever built for it.
        Returns the message index the trailing window starts at, so the caller renders only
        `messages[that index:]` itself."""
        split = max(0, total_messages - trailing_window)
        if split == 0:
            return 0
        chunk = _Chunk(0, split)
        self._sealed.append(chunk)
        await self._collapse(chunk, height_hint=split * ESTIMATED_LINES_PER_SEEDED_MESSAGE)
        logger.debug("Seeded a collapsed prefix covering %d of %d restored messages", split, total_messages)
        return split

    async def refresh_visibility(self) -> None:
        """Collapse the first sealed chunk found clearly outside the viewport, or expand the
        first collapsed placeholder found touching it. Mutates at most one chunk per call."""
        for chunk in self._sealed:
            if chunk.placeholder is None:
                # A margin buffer on the collapse side only (not the expand side, checked
                # below) gives a chunk that just expanded room to wobble near the boundary
                # without immediately re-collapsing.
                if not chunk.expanding and not chunk.collapsing and self._is_offscreen(
                    chunk, margin=self._container.size.height,
                ):
                    await self._collapse(chunk)
                    return
            elif not chunk.expanding and not self._is_offscreen(chunk):
                await self._expand(chunk)
                return

    async def expand_nearest_to_viewport(self) -> bool:
        """Expand whichever collapsed placeholder sits closest to the current scroll
        position. Returns whether one was found and expanded."""
        collapsed = [
            chunk for chunk in self._sealed if chunk.placeholder is not None and not chunk.expanding]
        if not collapsed:
            return False
        scroll_y = self._container.scroll_y
        nearest = min(collapsed, key=lambda chunk: abs(self._chunk_top(chunk) - scroll_y))
        await self._expand(nearest, reveal=True)
        return True

    def has_collapsed_chunks(self) -> bool:
        return any(chunk.placeholder is not None for chunk in self._sealed)

    def _accumulate(self, start: int, end: int, widgets: list[Widget]) -> None:
        if end <= start:
            return
        if self._pending is None:
            self._pending = _Chunk(start, end, widgets)
        else:
            self._pending.end_index = end
            self._pending.widgets.extend(widgets)
        if self._pending.message_count >= self._chunk_size:
            self._sealed.append(self._pending)
            self._pending = None
        self._call_after_refresh(self.refresh_visibility)

    def _chunk_top(self, chunk: _Chunk) -> int:
        widgets = [chunk.placeholder] if chunk.placeholder is not None else chunk.widgets
        if not widgets:
            return 0
        return min(widget.virtual_region.y for widget in widgets)

    def _is_offscreen(self, chunk: _Chunk, margin: int = 0) -> bool:
        widgets = [chunk.placeholder] if chunk.placeholder is not None else chunk.widgets
        if not widgets:
            return True
        top = min(widget.virtual_region.y for widget in widgets)
        bottom = max(widget.virtual_region.y + widget.virtual_region.height for widget in widgets)
        viewport_top = self._container.scroll_y - margin
        viewport_bottom = self._container.scroll_y + self._container.size.height + margin
        return bottom <= viewport_top or top >= viewport_bottom

    def force_layout(self) -> None:
        """Synchronously run Textual's layout pass so a widget just mounted or removed has an
        up-to-date `virtual_region` to measure immediately. `wait_for_refresh()` schedules its
        callback through the same queue `refresh_visibility()` is often already running from,
        and empirically does not reliably fire again from within that reentrant chain."""
        try:
            self._container.screen._refresh_layout()
        except NoScreen:
            pass

    async def _collapse(self, chunk: _Chunk, *, height_hint: int | None = None) -> None:
        if chunk.collapsing:
            return
        chunk.collapsing = True
        try:
            above_viewport = self._chunk_top(chunk) < self._container.scroll_y
            old_widgets = chunk.widgets
            removed_height = sum(widget.virtual_region.height for widget in old_widgets)
            placeholder = HistoryPlaceholder(
                chunk.message_count, lambda: self._expand_with_click(chunk),
                height=height_hint if height_hint is not None else removed_height)
            if old_widgets:
                await self._container.mount(placeholder, before=old_widgets[0])
            else:
                await self._container.mount(placeholder)
            if above_viewport:
                self.force_layout()
                delta = placeholder.virtual_region.height - removed_height
                new_y = max(0.0, self._container.scroll_y + delta)
                self._container.scroll_to(y=new_y, animate=False, immediate=True)
            for widget in old_widgets:
                await widget.remove()
            chunk.widgets = []
            chunk.placeholder = placeholder
            logger.debug(
                "Collapsed chunk(%d,%d) (%d messages) into one placeholder",
                chunk.start_index, chunk.end_index, chunk.message_count)
        finally:
            # The scroll watcher this collapse's own `scroll_to()` call re-triggers is itself
            # asynchronous, so clearing `collapsing` only once that reaction has run keeps it
            # from starting a second collapse of this same chunk before this one has finished.
            self._call_after_refresh(lambda: setattr(chunk, "collapsing", False))

    async def _expand_with_click(self, chunk: _Chunk) -> None:
        """`HistoryPlaceholder.on_click`'s callback: expand `chunk`, scrolling to reveal it."""
        await self._expand(chunk, reveal=True)

    async def _expand(self, chunk: _Chunk, *, reveal: bool = False) -> None:
        """Replace `chunk`'s placeholder with its real widgets. If `reveal`, scroll to where
        the placeholder was so the caller actually sees what it asked to expand; otherwise
        keep whatever's currently on screen from moving by compensating `scroll_y` for the
        added height."""
        placeholder = chunk.placeholder
        if placeholder is None or chunk.expanding:
            return
        chunk.expanding = True
        try:
            placeholder_y = placeholder.virtual_region.y
            placeholder_height = placeholder.virtual_region.height
            widgets = self._render_range(chunk.start_index, chunk.end_index)
            await self._container.mount(*widgets, before=placeholder)
            await placeholder.remove()
            chunk.widgets = widgets
            chunk.placeholder = None
            if reveal:
                self._container.scroll_to(y=placeholder_y, animate=False, immediate=True)
            else:
                # Auto-expand only fires for a placeholder overlapping the viewport, so
                # compensating unconditionally keeps visible content from jumping when it's replaced.
                self.force_layout()
                added_height = sum(widget.virtual_region.height for widget in widgets)
                delta = added_height - placeholder_height
                new_y = max(0.0, self._container.scroll_y + delta)
                self._container.scroll_to(y=new_y, animate=False, immediate=True)
            logger.debug(
                "Expanded chunk(%d,%d) (%d messages), reveal=%s",
                chunk.start_index, chunk.end_index, chunk.message_count, reveal)
        finally:
            # `_on_history_scroll_changed` is itself `async def`, so Textual defers its
            # reaction to the `scroll_to()` call above rather than running it inline here.
            # Clearing `expanding` only once that reaction has run keeps it from re-collapsing
            # this chunk before anyone sees it expanded.
            self._call_after_refresh(lambda: setattr(chunk, "expanding", False))
