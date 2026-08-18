# Plan 023: TUI history virtualization

## Problem

`#history` (`klorb/src/klorb/tui/mixins/rendering.py`) and `#subagent-history`
(`klorb/src/klorb/tui/mixins/subagents_panel.py`) are plain Textual `VerticalScroll` containers.
Every message becomes one or more permanently-mounted live widgets (`Static`/`Markdown`/
`ToolCallStatic`) via `_mount_response_widget`/`_mount_thinking_widget`/`_mount_tool_call_widget`/
`_mount_running_tool_call_widget` and their subagent-view equivalents (`_mount_subagent_messages`).
Nothing is ever unmounted except the transient `TurnWaitingStatic`. This is already flagged in
`TODO.md` ("In a long-enough session (2 hrs?) the TUI gets unusable and eventually crashes...
Definitely need to start pruning the rendered history in the DOM at a certain point.") This plan
implements that pruning.

A restored session (`_mount_restored_history`) has the identical problem at a different angle: it
mounts the *entire* persisted history up front on load, so a long saved session is slow to open
even before any new content streams in.

## Key architectural fact this plan leans on

`session.messages` (a `list[ChatMessage]`) is always the authoritative source of history content.
DOM widgets are a pure, stateless rendering of that list -- proven by the fact that
`_mount_restored_history` already reconstructs the full widget tree from `session.messages` alone,
with no other input. This means unmounting a widget never loses data: it can always be
reconstructed later by re-rendering the same message range. Virtualization is purely a DOM-cost
optimization, not a data-retention concern. Trimming `session.messages` itself (context
compaction) is a separate, already-tracked concern (TODO.md's "Context auto-compaction") and is
explicitly out of scope here.

## Approach: chunked mount/unmount, not per-pixel virtualization

Textual has no built-in windowed/virtual list widget comparable to `react-window`; `VerticalScroll`
mounts whatever children it's given and lays out the whole thing. Rather than trying to
per-pixel-virtualize (precise height accounting for arbitrary wrapped Markdown/tool-call content,
which Textual doesn't expose cheaply before layout), this plan uses **chunked virtualization**:

* History is grouped into chunks of a fixed number of *messages* (not widgets -- one message can
  produce 1-2 widgets, e.g. a `<Thinking>` label + body). A reasonable starting chunk size is
  something on the order of 20-30 messages; this should be tuned empirically once a prototype
  exists, not treated as fixed by this plan.
* The trailing chunk(s) -- anything covering the currently in-flight turn -- are never eligible
  for unmounting. Live-streaming updates (`_update_response_widget`, `_update_thinking_widget`,
  `_finalize_running_tool_call_widget`) hold direct references to mounted widget objects captured
  in per-turn closures; unmounting a widget a live closure still points at would silently break
  streaming into it. Only chunks that are fully settled (no message in the chunk is part of an
  active turn) are eligible.
* A chunk older than the trailing window and currently off-screen is replaced with a single
  lightweight placeholder widget ("— N earlier messages, press ... to expand —") in place of its
  real widgets. The placeholder's screen height does not need to match the removed content's exact
  height; see "Open design question: scroll position" below for how to handle the resulting scroll
  jump.
* A placeholder expands back into its real widgets (re-rendered from `session.messages[start:end]`
  using the exact same `_mount_*_widget`/`_render_tool_call` helpers a live turn or
  `_mount_restored_history` already uses) when the user scrolls it into or near the viewport, or
  via an explicit keybinding while the placeholder is focused/selected -- do not rely on a
  click-to-expand affordance alone, since `TODO.md` already notes mouse-based interaction is
  unreliable in this TUI ("mouse-based select/copy/paste doesn't work").
* `_mount_restored_history` changes to mount only the trailing chunk(s) of a restored session by
  default, with earlier chunks starting collapsed as placeholders -- fixing the "opening an old
  long session is slow" half of the problem, not just "staying open for 2 hours is slow."

## A shared abstraction, used by both `#history` and `#subagent-history`

`rendering.py`'s mount functions and `subagents_panel.py`'s `_mount_subagent_messages`/
`_render_full_subagent_transcript`/`_append_new_subagent_messages` are already near-duplicates of
each other (the Explorer investigation that preceded this plan confirmed this directly). Building
chunking logic separately into each would make that duplication permanently worse. Per
AGENTS.md's guidance against near-clone functions and in favor of encapsulating related
state/behavior in a class, this plan introduces one class -- something like a
`VirtualizedHistoryContainer` (exact name TBD by the implementer) -- that:

* wraps a `VerticalScroll` (`#history` or `#subagent-history`)
* is handed a `list[ChatMessage]` (or a slice of one) plus a render callback that turns a message
  range into mounted widgets, reusing the existing `_mount_*_widget`/`_render_tool_call`/
  `_render_restored_tool_call` helpers rather than reimplementing rendering
* owns chunk bookkeeping: which chunks are currently expanded vs. collapsed-to-placeholder, and
  the scroll-proximity check (reusing the existing `_on_history_scroll_changed`/
  `_on_subagent_history_scroll_changed` + `pinned_to_bottom` pattern already in place for
  pin-to-bottom tracking) that decides when a placeholder should expand.

This does not have to be a from-scratch widget subclass replacing `VerticalScroll` -- it can be a
plain Python class that operates on an existing mounted `VerticalScroll`, the same way
`RenderingMixin` and `SubagentsPanelMixin` operate on `#history`/`#subagent-history` today.
Whichever shape it takes, `RenderingMixin` and `SubagentsPanelMixin` should both delegate their
mount/unmount bookkeeping to it rather than keeping two independent implementations.

## Open design question: scroll position on unmount

Removing mounted widgets and replacing them with a smaller placeholder changes `VerticalScroll`'s
total content height, which will shift `scroll_y` under the user unless compensated for. The
simplest safe rule -- **never unmount a chunk that is currently visible in the viewport** -- avoids
the worst case (content disappearing out from under the reader's eyes) but does not fully avoid a
scroll-offset jump when a chunk *just above* the viewport collapses while the user is scrolling.
Whether this needs precise height-delta compensation (capturing each chunk's rendered height before
removal, then adjusting `scroll_y` by the same delta after mounting the placeholder) or whether a
coarser "only collapse chunks once they're already comfortably off-screen, tolerate a small jump for
returning readers" is good enough in practice is left for the implementing agent to resolve via a
quick prototype against Textual's actual scroll/mount APIs, rather than prescribed here without
having verified Textual's exact layout-timing guarantees firsthand.

## Implementation phases

### Phase 1: Chunking abstraction, `#history` only, no restored-session change yet

* Introduce the shared chunk-bookkeeping class described above.
* Wire it into `RenderingMixin` for `#history` only: chunk boundaries tracked as new messages
  stream in; older, settled, off-screen chunks collapse to placeholders; placeholders expand on
  scroll-proximity and via a keybinding.
* `_mount_restored_history` is unchanged in this phase (still mounts everything up front) -- keeps
  this phase's surface area to "stay open for 2 hours" only.
* Add Pilot-based integration tests (`klorb/tests/klorb/tui/mixins/test_rendering.py` already uses
  this pattern) simulating a long session and asserting the live widget count stays bounded while
  `session.messages` grows past several chunk boundaries, and that scrolling up re-expands the
  expected content.

### Phase 2: Restored-session startup, and subagent transcript view parity

* `_mount_restored_history` mounts only the trailing chunk(s), leaving earlier history collapsed.
* `SubagentsPanelMixin`'s `_render_full_subagent_transcript`/`_append_new_subagent_messages` are
  ported onto the same shared chunking class, removing the duplicate unbounded-mount logic there.
* Add/extend Pilot tests in `klorb/tests/klorb/tui/mixins/test_subagents_panel.py` covering the
  subagent transcript view's chunking behavior.

### Phase 3: Tuning and follow-up

* Empirically tune chunk size and the scroll-proximity expand threshold against a real long
  session, rather than shipping the Phase 1 placeholder values as final.
* Re-check the still-open `TODO.md` "gets unusable and eventually crashes... probably runaway
  threads or memory overrun" entry once this plan lands: if virtualization alone resolves the
  degradation, remove that half of the entry; if instability persists, it corroborates that a
  separate non-DOM cause (see the thread-leak investigation TODO.md entry this plan's sibling
  investigation added) is also contributing, and that entry should stay open on its own.
