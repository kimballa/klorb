# Plan 024: VSCode plugin history virtualization

**Current status: phases 1 and 2 complete; phase 3 pending.**

## Problem

`HistoryView` (`vscode-plugin/src/webview/features/history/components/HistoryView.tsx`)
unconditionally maps every entry in its `entries` array to a JSX `Entry` element:

```tsx
{entries.map((entry, index) => (
  <Entry key={index} entry={entry} index={index} ... />
))}
```

This is the webview's exact counterpart to the TUI's unbounded `#history`
`VerticalScroll` (see Plan 023, its sibling plan for the TUI side) -- no windowing, nothing ever
unmounted, one live DOM subtree per message forever. `SubagentTranscriptView.tsx` reuses
`HistoryView` wholesale for a selected subagent's transcript, so it inherits the same problem
rather than needing a separate fix.

This is a pure webview-side rendering concern. Per this codebase's own vscode-plugin planning
convention (see `docs/adrs/00156-vscode-webview-stays-acp-ignorant-behind-typed-messages.md` and
the ACP/host/webview three-layer split it documents): the full history array already arrives in
the webview today via the existing `HostMessage` protocol (`applyHostMessage` /
`applySessionReplay` in `historyModel.ts`) -- confirmed by reading that reducer directly, not
assumed. This plan adds no new ACP extension method, no new `HostMessage`/`WebviewMessage`
variant, and no extension-host translation work: the data virtualization needs is already on the
wire. Everything in this plan is scoped to `vscode-plugin/src/webview/features/history/`.

## What's already there to build on

* `usePinnedScroll` (`vscode-plugin/src/webview/hooks/usePinnedScroll.ts`) already tracks
  scroll-pinned-to-bottom state for both `App.tsx`'s root `#history` and
  `SubagentTranscriptView.tsx`, via `scrollTop`/`scrollHeight`/`clientHeight` reads and a
  `scrollToBottomIfPinned()`/`scrollToBottom()` API.
* `historyModel.ts`'s `applyHostMessage` reducer is already the single point where the flat
  `entries` array is built/extended -- `extendStreamingText` mutates the trailing streaming entry
  in place (by replacing the last array element) rather than the whole array, so the *protocol*
  side is already incremental; it's specifically the *rendering* side (`HistoryView`'s
  unconditional `.map()`, no `React.memo`, index-based `key`) that is unbounded and non-memoized.

## What this plan changes

Two related but distinct performance problems exist today and both need fixing, not just DOM node
count:

1. **DOM node count**: every message ever added stays mounted forever.
2. **Re-render cost on every update**: `entries` is a single flat array in `App.tsx` `useState`;
   every `postMessage` (including a single streaming chunk) produces a new array reference, which
   re-invokes `HistoryView`'s `.map()` over *every* entry, and `Entry` is not `React.memo`'d, so
   every earlier, unrelated entry (including full `ReactMarkdown` re-parses) re-renders on every
   chunk of a streaming response. This is a real cost even without addressing DOM count at all,
   and windowing alone does not fix it for whatever remains inside the rendered window.

### Approach

* **Memoize `Entry`** (`React.memo`, with a stable comparison -- likely keyed off whatever fields
  actually change, e.g. `entry` identity plus `streaming` state) so an update to the trailing
  streaming entry does not re-render every earlier entry. This is a small, independently valuable
  fix that should land first and can be validated on its own before windowing is added.
* **Windowing**: introduce an actual virtualization strategy for `HistoryView`'s list rendering
  -- unlike the TUI (Plan 023), which has no library-level windowed-list primitive to reach for,
  React has mature options (e.g. `react-window`/`react-virtual`-family libraries, or a hand-rolled
  IntersectionObserver-based mount/unmount). Given `entries` already holds heterogeneous,
  variable-height content (markdown, tool-call diffs, thinking blocks), prefer a
  variable-size-aware windowing approach over one that assumes fixed row heights -- pick a
  concrete library or approach during implementation rather than prescribing one here, but the
  choice must support variable-height rows and must not require any new dependency capable of
  reaching outside the extension's normal npm dependency review (see `add-python-dependency`-style
  scrutiny; there isn't a vscode-plugin equivalent skill, so use ordinary judgment: pick a small,
  well-maintained, actively-used library and add it via `npm install` in `vscode-plugin/`, not a
  hand-rolled reimplementation unless a reasonable library genuinely doesn't fit).
* Stable, non-index `key`s: `entries` is append-only today (confirmed by the reducer), but
  windowing implementations generally assume mount/unmount churn and behave better with stable
  keys than array-index keys regardless. If `HistoryEntry` doesn't already carry a stable id
  usable as a key, add one at construction time in `historyModel.ts` rather than deriving one at
  render time.
* `usePinnedScroll` needs to keep working under a windowed list: "the last element" and raw
  `scrollHeight`/`scrollTop` math may need to route through whatever API the chosen windowing
  library exposes for "scroll to end"/"is scrolled to end" instead of DOM-querying
  `lastElementChild` directly. Treat this as required adaptation, not an incidental side effect --
  a virtualization change that silently breaks pin-to-bottom tracking would be a regression a user
  would notice immediately.
* `SubagentTranscriptView.tsx` reuses `HistoryView`, so it gets this fix automatically as long as
  the change is made inside `HistoryView`/`historyModel.ts` rather than duplicated -- verify this
  explicitly with a test rather than assuming it, since Plan 023's TUI-side investigation found
  the two analogous TUI code paths had silently drifted apart over time despite looking like they
  should share behavior.
* `vscode.setState({entries, ...})` (`App.tsx`) persists the full array on every entries change.
  Windowing the *rendered* DOM doesn't reduce this cost -- it's a separate O(n)-per-update
  serialization cost against the full logical history, not the rendered window. Flag as an
  explicit follow-up rather than folding into this plan's scope: it may warrant its own pruning or
  debouncing strategy, but changing what gets persisted to `vscode.setState` risks changing session
  restore behavior in ways this plan doesn't need to touch.

## Implementation phases

### Phase 1: Memoize `Entry`

* Wrap `Entry` in `React.memo`; add/adjust props so the comparison is meaningful (avoid a
  memo that never actually skips a re-render because of an unstable prop like an inline callback).
* Add or extend tests under `test/webview/features/history/` asserting that appending a streaming
  chunk to the trailing entry does not re-render an unrelated earlier entry (a render-count spy or
  equivalent).

### Phase 2: Windowed rendering

* Introduce windowing for `HistoryView`'s list, per "Approach" above. Keep `HistoryView`'s public
  props (`entries`, `historyRef`, etc.) stable so `SubagentTranscriptView.tsx` and `App.tsx` don't
  need call-site changes beyond what the new windowing library's own container/ref requirements
  demand.
* Adapt `usePinnedScroll` to the windowed container's scroll API.
* Give `HistoryEntry` a stable id (if it doesn't already have one) and switch `key={index}` to it.
* Add Pilot-equivalent integration tests (this codebase's webview test harness, mirrored under
  `test/webview/features/history/`) simulating a long `entries` array and asserting the live
  rendered DOM node count stays bounded, and that scrolling up re-renders earlier content.

### Phase 3: Verify subagent transcript parity and tune

* Explicit test coverage confirming `SubagentTranscriptView.tsx` inherits both the memoization and
  windowing fix with no separate code path silently missing them.
* Tune window size / overscan against a real long session transcript, the same empirical-tuning
  spirit as Plan 023's Phase 3.
