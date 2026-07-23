# Use React for the webview UI, bundled with esbuild

* Date: 2026-07-23 00:20
* Question: The "Klorb session" webview started as hand-rolled DOM code (`document.
  getElementById()`, manual `appendChild()`/`textContent` for each history entry, a manual
  `keydown`/`click` wiring). That's fine for "one append-only list of plain-text entries, one
  textarea, one button," but the panel exists to eventually render whatever `klorb server`
  (`docs/specs/klorb-server.md`) sends back — structured messages, streaming tool-call output,
  the same kind of rendering complexity klorb's own Textual-based TUI already has. Is it worth
  introducing a UI framework into the webview now, while the surface area is still small, or is
  that premature for a stub that currently does nothing but echo typed text into a `<div>`?
* Answer: Yes — React, bundled into the existing esbuild pipeline. `src/webview/main.tsx` calls
  `acquireVsCodeApi()` once and mounts `<App vscode={vscode} initialEntries={...} />` via
  `react-dom/client`'s `createRoot()` into a single `<div id="root">` that
  `KlorbSessionViewProvider._getHtml()` now renders instead of writing the title/history/input
  markup into the HTML string directly. `src/webview/App.tsx` owns all of the panel's
  interactive state (`entries`, the draft textarea value) as ordinary `useState`; `esbuild
  --bundle` inlines `react`, `react-dom/client`, `App.tsx`, and `keyHandling.ts` into one
  dependency-free `out/webview/main.js`, the same `<script nonce="...">`-loaded, no-ES-module
  shape established in `docs/adrs/bundle-webview-script-with-esbuild-not-es-modules.md`.
  `--define:process.env.NODE_ENV="development"` is passed (no `--minify`): `make
  install`/`compile` is exclusively the local dev loop today — nothing here ships to real users
  yet — so the build is tuned for debuggability over size. `NODE_ENV="development"` keeps
  React's own warnings (missing list keys, hook-rule violations, invalid props) and full error
  text intact; `NODE_ENV="production"` strips both, replacing errors with a minified code plus
  a URL to decode it, exactly the kind of signal that mattered during this extension's own
  webview-script debugging (see `docs/adrs/call-acquirevscodeapi-exactly-once-per-webview-page.
  md`). This was measured both ways for this stub: `--minify --define:...="production"` yields
  a 190KB bundle; `--define:...="development"` without `--minify` yields 1.1MB. That's an
  acceptable trade for a webview loaded once from local disk with no network fetch involved —
  size only starts to matter once this extension is actually packaged for other people to
  install, which is a distinct, not-yet-needed step (a `RELEASE=1`-style Makefile variable
  switching to `--minify --define:...="production"`, added when real distribution is actually
  on the table, rather than now). `react`/`react-dom` are `devDependencies`, not
  `dependencies`, regardless of build mode: nothing in the packaged extension `require()`s them
  at runtime, since esbuild inlines them into the bundle either way, so a `dependencies` entry
  would only cause `vsce`/`npm ci --omit=dev` to try to ship or install a redundant
  `node_modules/react` alongside code that already contains it.
* Reasoning: React (or an equivalent framework) in a VS Code webview is well-trodden, not
  unusual — GitLens, GitHub Pull Requests, and Copilot Chat all bundle a framework into their
  webview scripts the same way, subject to the same CSP/single-`acquireVsCodeApi()`-call
  constraints this extension already had to work through. The deciding factor for *when* to
  introduce it here wasn't "frameworks are generally good" but the specific trajectory this
  panel is on: `[[klorb-server]]` interop means rendering structured, possibly-streaming
  messages (tool-call cards, multi-part turns) rather than plain strings, which is exactly the
  kind of state-driven, frequently-re-rendered UI where hand-rolled DOM mutation gets
  error-prone fast (the earlier vanilla version already needed a `historyRef`-equivalent manual
  scroll-into-view and manual state persistence; both are ordinary `useState`/`useEffect` in the
  React version). Doing it now, while the component is still small (one `App.tsx`, ~70 lines),
  means the migration is contained and easy to verify end-to-end, rather than retrofitting a
  framework onto a larger hand-rolled DOM tree later once `klorb server` integration is also in
  flight.

  Adopting React surfaced one more instance of the same TypeScript module-resolution issue
  `tsconfig.json` already hit for the extension host (`undici-types` failing to resolve under
  the pre-2021 "classic" resolution algorithm): `@types/react`'s own `package.json` `exports`
  map (its `jsx-runtime` subpath, `csstype`) doesn't resolve either, without a resolution mode
  that understands `exports` maps. `tsconfig.webview.json` uses `moduleResolution: "bundler"`
  rather than `"nodenext"` for this — deliberately different from the extension host's setting
  — because `"bundler"` is the mode TypeScript documents as intended for exactly this situation:
  code that will only ever be consumed by a bundler (esbuild, here) and never executed via a
  real Node `require()`/ESM loader, which is a strictly more accurate description of
  `src/webview/*` than `"nodenext"` (a real Node module system) would be.

  The alternative of keeping the webview as hand-rolled DOM code until `klorb server`
  integration actually starts was considered and rejected: it would mean introducing React at
  the same time as the first real structured-message rendering work, compounding the risk of
  two nontrivial changes (new framework *and* new message shapes) instead of landing the
  framework first against behavior that's already fully understood and tested.
