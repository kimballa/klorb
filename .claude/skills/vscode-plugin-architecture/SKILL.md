---
name: vscode-plugin-architecture
description: Reference for how vscode-plugin/src/ is organized -- the host/webview/shared split, the features/<name>/ barrel pattern, tsconfig path aliases, and the React default-export convention. Use whenever adding, moving, or importing a file under vscode-plugin/src/ or vscode-plugin/test/, or when a component/module's placement or import style is unclear.
---

# vscode-plugin source tree

`vscode-plugin/src/` is split by JavaScript runtime, not by feature, at the top level:

* `src/host/` — extension-host code (runs under Node, `require()`d by VS Code). The activation
  entry point (`extension.ts`, matching `package.json`'s `main`) stays directly under `src/`,
  sibling to `host/`, the same way the webview's entry point (`main.tsx`) stays directly under
  `src/webview/` rather than nested in a feature.
* `src/webview/` — webview UI code (runs in a sandboxed `vscode-webview://` document; React).
* `src/shared/` — types/utilities included by both the host and webview tsconfigs
  (`tsconfig.json` and `tsconfig.webview.json`) — e.g. the host↔webview message protocol.
* `types/` — ambient `.d.ts` declarations (e.g. the vendored `vscode-elements` JSX typings).
* `test/` mirrors the `src/` tree file-for-file (`test/host/`, `test/webview/`, `test/shared/`),
  including the `features/` nesting described below.

`src/webview/tsconfig.json` and `test/webview/tsconfig.json` are tiny pointer files
(`{"extends": "../../tsconfig.webview.json"}`) purely so VSCode's editor tooling
picks the right project: it only auto-discovers a file literally named `tsconfig.json` by
walking up from whatever file is open, so without these, opening a file under `src/webview/` or
`test/webview/` would find the *host* `tsconfig.json` (which excludes that subtree entirely) and
fall back to an "orphan file" with no `paths` aliases at all. The actual `tsc`/`tsgo`/`esbuild`
invocations always pass `-p tsconfig.webview.json` (or `-p ./`) explicitly, so these two files
are never referenced by any script and exist only for the editor's benefit.

Within `src/host/` and `src/webview/`, most code lives under a `features/<name>/` folder
(`src/webview/features/history/`, `src/host/features/acp/`, ...), following the "bulletproof
react" style: a feature's `index.ts` is the *only* module anyone outside that feature may
import — never deep-import a file from inside another feature
(`webview/features/history/historyModel` from outside `features/history/` is wrong; import
`webview/features/history` and let its `index.ts` re-export what's needed). Enforced by
`eslint.config.mjs`'s `no-restricted-imports` rule. Inside a feature, organize submodules
as-needed (`components/`, `hooks.ts`/`hooks/`, `types.ts`/`types/`, or plain
files). The barrel is what's contractual, not the internal shape.

Top-level `src/webview/
components/` and `src/webview/hooks/` (outside any `features/` folder) hold only pieces
genuinely universal across features (e.g. `VsCodeApiProvider`/`useVsCodeApi`), not specific to
one.

Every tsconfig (`tsconfig.json` for the host, `tsconfig.webview.json` for the webview) declares
`paths` aliases rooted at `src/`: `shared/*`, plus `host/*` (host tsconfig only) or `webview/*`
(webview tsconfig only). Never both in the same config. The host and webview must not
import each other's code. Applying the general Import Rules to vscode-plugin
specifically: relative imports (`./foo`, `../foo`) are reserved for imports inside
the *same* `features/<name>/` folder; every other import — including between top-level
non-feature files — uses the rooted alias form (`import PromptInput from
'webview/components/PromptInput'`, not `'./components/PromptInput'`).

`vitest.config.mts` uses
the `vite-tsconfig-paths` plugin (pointed at both tsconfigs via its `projects` option) so tests
resolve the same aliases; adding a new subtree under `test/` also requires adding it to the
matching tsconfig's `include` (see that file's comments) or the alias won't resolve for tests
rooted there.

React component/hook files (not plain utility/model modules like `historyModel.ts` or
`keyHandling.ts`, which keep named exports) export their component or hook as `export default`.
A feature's `index.ts` barrel re-exports a default-exported item by name (`export 
HistoryView from './components/HistoryView';`). Consumers still get a named import from the
barrel.
