# Bundle the webview script with esbuild instead of loading it as an ES module

* Date: 2026-07-22 23:40
* Question: `vscode-plugin`'s webview script (`src/webview/main.ts`) needs one function,
  `classifyEnterKey()`, from a second file (`keyHandling.ts`) so it can be reused by
  `test/keyHandling.test.ts` without re-implementing it. The first implementation compiled
  `src/webview/*.ts` with `module: "ES2022"` and loaded the result as `<script type="module"
  nonce="...">`, letting `main.js` `import` `keyHandling.js` at runtime the normal ES module
  way. The webview's `Content-Security-Policy` allows scripts only via a per-render nonce
  (`script-src 'nonce-...'`), the standard way a VS Code webview permits its own script while
  blocking anything else. With that setup, every symptom pointed at the script never running at
  all: Enter fell back to the textarea's native newline instead of submitting, the Send button
  did nothing, and Ctrl+Enter did nothing (rather than inserting a newline, its own native
  default). Should the fix be to adjust the CSP to also trust the imported module, or to stop
  loading the webview script as an ES module in the first place?
* Answer: Stop using `<script type="module">` for the webview entry point. `src/webview/main.ts`
  keeps its ordinary `import { classifyEnterKey } from './keyHandling'` in source — the reuse
  AGENTS.md's anti-duplication rule requires — but `esbuild --bundle --format=iife` resolves
  that import at build time and inlines `keyHandling.ts`'s one exported function directly into
  `out/webview/main.js`. The emitted file has no `import`/`export` statement left in it at all,
  so it loads as a plain `<script nonce="...">`, identical in kind to the CSP's existing
  single-script-trusted-by-nonce model. `tsconfig.webview.json` still type-checks
  `src/webview/*.ts` (now with `noEmit: true`) so a type error is still caught at `make
  lint`/`compile` time; esbuild only does the bundling emit.
* Reasoning: The CSP-side fix — adding `'strict-dynamic'` to `script-src` alongside the nonce,
  the documented way to let a nonce'd `<script type="module">` extend trust to its own
  statically-imported module graph — was tried first and didn't resolve the symptom either.
  Whether that's because VS Code's webview CSP enforcement under the `vscode-webview://` scheme
  doesn't extend `'strict-dynamic'` to module imports the same way a normal `https://` page's
  CSP does, or some other interaction specific to the webview sandbox, wasn't pinned down —
  and it didn't need to be, because avoiding the ES-module/CSP interaction entirely removes the
  question rather than depending on a browser behavior that couldn't be directly verified in
  this environment (no GUI VS Code available to inspect the webview's own devtools console).
  This also matches the shape of Microsoft's own `vscode-extension-samples` webview examples,
  none of which load a webview entry point as `type="module"` — a single bundled,
  dependency-free script is the well-trodden path for VS Code webviews specifically, not just a
  generic web-CSP workaround.

  The alternative of inlining `classifyEnterKey()`'s three-line body directly into `main.ts`
  (no second file, no import, no bundler needed) was rejected because it would duplicate logic
  that `test/keyHandling.test.ts` needs to import from somewhere — either the test file
  reimplements the same classification logic to compare against (defeating the point of the
  test) or `main.ts` and the test both import a shared module, which is exactly what bundling
  already provides without giving up the standalone, directly-tested pure function.
