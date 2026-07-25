# Production `.vsix` build is minified, prod-React, and ships no `node_modules`

* Date: 2026-07-24 23:07
* Question: `vscode-plugin`'s only build path (`npm run compile`, `make install`) was a
  development build: unminified `esbuild` bundles, `NODE_ENV=development` (so React ships its
  development-only checks/warnings), full sourcemaps with embedded original source, and
  `.vscodeignore` didn't exclude `node_modules/**` even though both the extension host and the
  webview are now fully bundled by `esbuild` (see
  `docs/adrs/bundle-extension-host-with-esbuild-not-tsc-emit.md`) — nothing at runtime actually
  `require()`s a package out of `node_modules` anymore except the VS Code host's own `vscode`
  module, which doesn't exist on disk at all. What does an actual "ship this" build need beyond
  what `make install` already does for local dev-loop use?
* Answer: A parallel `:prod` set of `esbuild` invocations
  (`build:extension:prod`/`build:webview:prod`, wired through `compile:prod`) adds `--minify`,
  `--define:process.env.NODE_ENV=\"production\"` (webview only — this is what lets `esbuild`'s
  minifier dead-code-eliminate React's `NODE_ENV !== "production"` branches, the single biggest
  size/perf win for shipping a React bundle), `--sourcemap=linked --sources-content=false` (a
  sourcemap still exists next to each bundle for symbolicating a crash, but doesn't embed a full
  readable copy of the original TypeScript source the way a dev sourcemap's default
  `sourcesContent` does), and `--legal-comments=linked` (collects bundled dependencies' license
  comments into a `.LEGAL.txt` next to each bundle instead of leaving them inline in already
  minified code, or silently dropping the attribution the MIT/etc. licenses of `react`,
  `@agentclientprotocol/sdk`, and the rest require). `.vscodeignore` now excludes
  `node_modules/**`, `package-lock.json`, and `types/**` (ambient `.d.ts` source, compile-time
  only) — since bundling already inlines every real dependency, none of that tree needs to ship
  in the `.vsix` at all. The `Makefile`'s new `dist` target runs `compile:prod` then `npm run
  package` (the same `vsce package` `install` already uses) — it deliberately doesn't also
  install the result into the local VS Code the way `install` does, since `dist` is for
  producing a distributable artifact, not iterating locally.
* Reasoning: The dev build's choices are each individually right for their purpose (unminified
  output is what a local `F5`/`Reload Window` debugging loop wants — readable stack traces,
  fast rebuilds, React's dev warnings surfacing real bugs during development) but wrong for
  what actually ships to a user: a multi-megabyte bundle with dev-mode React and every
  dependency duplicated a second time in `node_modules` inside the `.vsix` is neither smaller
  nor faster nor more secure than it needs to be. Keeping the dev scripts untouched and adding
  `:prod` variants (rather than parameterizing one script with an env var) keeps both paths
  simple, explicit `esbuild` command lines rather than introducing a mode-branching flag that
  every future flag change has to remember to gate correctly. `node_modules/**` was already dead
  weight in the packaged `.vsix` even before this change — bundling the extension host (not just
  the webview) removed the last real runtime dependency on it — this ADR is what caught and
  fixed that.
