# TUI history virtualization

## Problem

`#history` and `#subagent-history` are `VerticalScroll` containers that, without bounding,
accumulate one permanently-mounted widget per message forever, which degrades a long-running TUI
session. `VirtualizedHistoryContainer` (`klorb/src/klorb/tui/widgets/virtualized_history.py`)
bounds this by collapsing older, settled, off-screen content into a single placeholder widget per
chunk, re-expandable on demand.

## Chunking model

`session.messages` is the authoritative source of history content; every mounted widget is a
reconstructible rendering of some `session.messages[start:end]` range. `VirtualizedHistoryContainer`
tracks chunks as `(start_index, end_index, widgets)` over that message-index space, not over pixel
height. A chunk accumulates message ranges (via `close_trailing_region()` or
`register_settled_range()`) until it reaches `DEFAULT_CHUNK_SIZE_MESSAGES` messages, then seals;
only sealed chunks are ever collapsed, and only once none of their widgets are visible in the
container's current viewport (`refresh_visibility()`).

Content still being streamed into by an in-flight turn is exempt from collapse: a mixin brackets
a turn with `begin_trailing_region()`/`close_trailing_region()`, and only widgets mounted after
`close_trailing_region()` closes become chunk-eligible. This keeps a live-streaming widget
reference (e.g. `RenderingMixin._update_response_widget`'s `widget` argument) from ever being
unmounted out from under an update targeting it.

A collapsed chunk is represented by one `HistoryPlaceholder` `Static`. Expanding it re-renders its
message range via a `render_range` callback the owning mixin supplies, reusing
`RenderingMixin._render_message_range` -- the same message-to-widget builder used for restored and
live-turn rendering. Expansion happens on scroll proximity (`refresh_visibility()`, called from the
`#history`/`#subagent-history` `scroll_y` watcher), on click, or via the `Ctrl+E` "Expand"
keybinding, which expands whichever collapsed chunk sits closest to the current scroll position.

A runtime collapse (a chunk that scrolled offscreen after being rendered) sizes its placeholder to
the exact height of the widgets it's replacing, so the scrollbar's proportions don't shift and no
scroll compensation is actually needed for that direction. `seed_collapsed_prefix()` has no
rendered widgets to measure -- that's the point, restoring a long session never builds them -- so
it sizes its placeholder using `ESTIMATED_LINES_PER_SEEDED_MESSAGE`, a rough per-message line-count
heuristic. Without it, a placeholder standing in for hundreds of never-rendered messages would
report a height of exactly one line, making the scrollbar look almost entirely empty above the
still-rendered trailing window.

## Root history (`#history`)

`ReplApp` owns one `VirtualizedHistoryContainer` (`self._history_virtualizer`), built by
`RenderingMixin._new_history_virtualizer` and rebuilt whenever `/clear` replaces the session.
`PromptSubmissionMixin._submit_prompt` opens a trailing region before dispatching a turn;
`_finish_turn` closes it once the turn (including any queued-message follow-up) settles.
`RenderingMixin._mount_restored_history` seeds everything before the trailing
`DEFAULT_CHUNK_SIZE_MESSAGES` messages of a restored session as a single collapsed placeholder via
`seed_collapsed_prefix()`, so opening a long saved session only builds widgets for its tail.

## Subagent transcript (`#subagent-history`)

`#subagent-history` is fully rebuilt on every `SubagentsPanelMixin._select_session` call, so its
`VirtualizedHistoryContainer` (`self._subagent_history_virtualizer`) is rebuilt alongside it in
`_render_full_subagent_transcript`, bound to the newly selected session. Since a subagent's
messages are only ever appended once complete (no per-token streaming into this view), every delta
`_append_new_subagent_messages` mounts is immediately settled and registered via
`register_settled_range()` rather than needing a trailing-region bracket.

## Scroll-position handling

Expanding and collapsing use different, deliberately asymmetric proximity checks
(`VirtualizedHistoryContainer._is_offscreen`'s `margin`): a placeholder expands as soon as it
touches the viewport (margin `0`), but a chunk only collapses once it's a full viewport-height
past the edge (margin `container.size.height`). This hysteresis keeps a chunk that just expanded
from immediately re-collapsing on a small scroll wobble near the boundary.

Both collapsing and expanding a chunk can still change the container's total content height (an
estimated seed placeholder rarely matches its real content exactly), which would shift whatever's
currently on screen unless compensated for. `_collapse`/`_expand` measure the height of the
widgets being swapped in and out directly, forcing a layout pass via `force_layout()` first so
`virtual_region` reflects the mutation immediately rather than staying stale until Textual's own
debounced layout timer next fires. `force_layout()` calls Textual's private `Screen._refresh_layout()`
rather than the public `Widget.wait_for_refresh()`, since the latter schedules its callback through
the same queue `refresh_visibility()` is often already running from and empirically does not
reliably fire again from within that reentrant chain.
`_expand` applies the resulting delta to `scroll_y` on every auto-triggered (non-`reveal`)
expansion, since `refresh_visibility()` only auto-expands a placeholder that already overlaps the
viewport; the same is true of `_collapse`'s delta when the collapsing chunk sits above the
viewport. Both write the new position via `scroll_to(animate=False, immediate=True)` rather than
assigning `scroll_y` directly, since a mousewheel or scrollbar-drag scroll may still be animating
when the compensation runs, and a plain attribute write would just get overwritten by the
animation's next tick. The exception is `_expand` with `reveal=True` (the `Ctrl+E` keybinding and
click-to-expand): there, the point is to show the caller what it just asked to expand, so the
view scrolls to where the placeholder was instead of holding still.

A mutation's resulting `scroll_y` write re-triggers `#history`/`#subagent-history`'s `scroll_y`
watcher, which is itself asynchronous, so neither `_expand` nor `_collapse` clears a chunk's
`expanding`/`collapsing` flag until that reaction has had its own turn to run. Clearing either flag
eagerly lets the watcher's reentrant `refresh_visibility()` call see a mid-flight chunk as fair
game and mutate it a second time before the first mutation has finished. Without `collapsing`
specifically, mounting a placeholder and removing several old widgets is enough real `await` work
for a second `_collapse()` call on the same chunk to start before the first one returns, mounting a
second placeholder that orphans the first in the DOM.

## Known follow-up

`DEFAULT_CHUNK_SIZE_MESSAGES`, the collapse-side hysteresis margin, and
`ESTIMATED_LINES_PER_SEEDED_MESSAGE` are untuned starting values; see the "Plan 023" section of
`TODO.md`.
