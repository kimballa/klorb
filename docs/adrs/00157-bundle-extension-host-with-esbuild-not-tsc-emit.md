# Bundle the extension host with esbuild; tsc/tsgo only typecheck it

* Date: 2026-07-24 22:35
* Question: `vscode-plugin`'s webview code used `paths` aliases rooted at `src/` (`shared/*`,
  `webview/*`) so imports could read `import x from 'shared/foo'` instead of relative
  `'../../shared/foo'`, resolved at build time by `esbuild` bundling `src/webview/main.tsx`.
  Extending the same `shared/*`/`host/*` aliases to the extension host's own tsconfig
  (`tsconfig.json`) made them type-check under `tsc -p ./`, but that project was never bundled —
  `tsc -p ./` emitted one `.js` file per source file into `out/`, preserving the folder
  structure, and VS Code's `require()` (via `package.json`'s `main: ./out/extension.js`) loaded
  the result directly. `tsc` only type-checks; it doesn't rewrite non-relative import
  specifiers. Would the host's compiled output actually resolve `require('host/klorbServerProcess')`
  at runtime, or does the host need bundling too?
* Answer: No — plain `tsc` emit does not rewrite `paths`-aliased specifiers, so `out/
  extension.js` would `require('host/klorbServerProcess')` verbatim, and Node would try (and
  fail) to resolve it as an npm package. The extension host is now bundled with `esbuild` too
  (`src/extension.ts`, `--bundle --platform=node --format=cjs --external:vscode`), mirroring the
  webview's existing build step, so the aliases resolve at bundle time regardless of Node's own
  runtime resolution. `tsconfig.json` gained `"noEmit": true` (matching `tsconfig.webview.json`,
  which was already type-check-only) since `esbuild` now owns all real emission; `tsc`/`tsgo -p
  ./` exist purely to type-check the host project. `package.json`'s `compile` script runs
  `typecheck` then both `build:extension` and `build:webview`; the `Makefile`'s `compile`/
  `typecheck`/`lint`/`test` targets just call the matching `npm run` script, so the canonical
  command lines live in one place (`package.json`) instead of being duplicated across the
  `Makefile` and `package.json`.
* Reasoning: The alternative — making the aliases resolve at actual Node runtime without
  bundling — would require either Node's native `imports` field (which mandates a `#`-prefixed
  specifier like `#shared/foo`, not the plain `shared/foo` form wanted here) or a runtime
  resolver hook (`tsconfig-paths/register`, `module-alias`), neither of which VS Code's
  extension-host loader gives a clean place to inject. Bundling sidesteps the whole
  runtime-resolution question — once `esbuild` has inlined everything into one file, there's no
  remaining `require('host/...')` call for Node to resolve at all — and it's the same mechanism
  the webview already used successfully, so the host gains no new build tool, just a second
  invocation of one already in the dependency tree. It also matches VS Code's own documented
  guidance to bundle extensions for faster activation.
