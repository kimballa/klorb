# Call `acquireVsCodeApi()` exactly once per webview page load

* Date: 2026-07-22 23:47
* Question: `vscode-plugin`'s webview script (`src/webview/main.ts`) initially had
  `main()` call `acquireVsCodeApi()` once to get the `vscode` object it needed for
  `setState()`, then call a separate `restoreState()` helper that called
  `acquireVsCodeApi()` again to read `getState()`. Every interactive behavior in the panel —
  Enter submitting, Ctrl+Enter inserting a newline, the Send button — was completely inert,
  and neither the `secondarySidebar` manifest fix, the `--force` reinstall fix, nor bundling
  the webview script with esbuild (to remove an ES-module/CSP interaction) changed that. What
  was actually wrong?
* Answer: The webview's own devtools console (`Developer: Open Webview Developer Tools`)
  showed the real error directly: `Uncaught Error: An instance of the VS Code API has already
  been acquired`, thrown from the second `acquireVsCodeApi()` call inside `restoreState()`.
  VS Code's webview-injected `acquireVsCodeApi()` throws on any call past the first one in a
  given page load. Nothing in `main()` caught that exception, so it propagated out of the
  top-level `main();` call and aborted the script right there — meaning every
  `addEventListener()` call written *after* the `restoreState()` call in source order never
  ran at all. `main()` now calls `acquireVsCodeApi()` exactly once and threads the resulting
  `vscode` object through to every place that needs it (the initial `getState()` read and
  `submit()`'s `setState()` call), instead of re-deriving it in a helper.
* Reasoning: This explains every symptom that survived three prior fix attempts, because none
  of those attempts touched the one line actually throwing. The CSP/ES-module fix (see
  `docs/adrs/bundle-webview-script-with-esbuild-not-es-modules.md`) and the `secondarySidebar`
  manifest key were both real, independently necessary fixes for the problems they targeted —
  they just weren't *this* problem, and there was no way to distinguish "still broken because
  of a different bug" from "still broken because the fix didn't take effect" without a live
  error message. Static reasoning about CSP and module semantics, however well-founded, cannot
  substitute for the one artifact that pins down a runtime failure in already-loaded,
  already-installed code: the browser console the code is actually throwing into. Once that
  was available, the fix was a two-line change with a well-documented, easily searched cause
  (`acquireVsCodeApi` is documented as single-call-only), not a guess.

  This is also why `main.ts` threads `vscode` through as a plain parameter/closure variable
  rather than, say, memoizing `acquireVsCodeApi()`'s result behind a lazy-init wrapper: the
  webview has exactly one entry point (`main()`) and one page load per webview instantiation,
  so there is no second call site that legitimately needs its own acquisition — a guard/memoize
  wrapper would paper over a call-graph shape (something other than `main()` reaching for the
  API) that shouldn't exist here in the first place.
