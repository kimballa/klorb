2026-08-18

## Question

`HistoryView` (`vscode-plugin/src/webview/features/history/components/HistoryView.tsx`) needs
to window its rendering so a long session doesn't keep every message mounted in the DOM
forever (Plan 024). `entries` holds heterogeneous, variable-height content -- markdown
responses, tool-call diffs, thinking blocks -- so a fixed-row-height virtualizer won't fit.
Which windowing approach should it use?

## Answer

`react-virtuoso`'s `Virtuoso` component. `HistoryView` renders it directly (`id="history"`,
`computeItemKey` keyed off `HistoryEntry.id`, a custom `Item` wrapper for CSS spacing/
alignment hooks), and `usePinnedScroll` (`vscode-plugin/src/webview/hooks/usePinnedScroll.ts`)
drives it through its own `atBottomStateChange`/`scrollToIndex` imperative API instead of raw
`scrollTop`/`scrollHeight` DOM reads.

## Reasoning

`react-virtuoso` measures each item's real rendered height via `ResizeObserver` rather than
assuming a fixed row height, so it fits this entry list's variable-height content without a
hand-rolled measurement pass. It's MIT-licensed, has no runtime dependencies, and is actively
maintained. It also ships `VirtuosoMockContext`, a context provider that substitutes a fixed
`viewportHeight`/`itemHeight` for real DOM measurement -- necessary because jsdom (this
project's unit-test environment) never fires `ResizeObserver` callbacks with a real size, so a
`Virtuoso` list mounted under jsdom without it renders zero items and stays stuck there even as
`data` grows across rerenders. Tests that need entries to actually appear in the DOM wrap
`render()` in that provider with a generous viewport (see `App.test.tsx`'s local `render`
wrapper and `HistoryView.test.tsx`'s windowing tests, which use a small one instead to assert
the rendered node count stays bounded).

`react-window`'s `VariableSizeList` was the other candidate; it was rejected because it
requires the caller to supply (or actively re-measure) each row's height up front rather than
observing it, which doesn't fit this list's markdown/diff/thinking-block content without a
separate measurement pass `react-virtuoso` gets for free.
