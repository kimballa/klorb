# VS Code plugin

## Summary

`vscode-plugin/` is a VS Code extension that docks a "Klorb session" panel in the editor's
secondary side bar (the right-hand auxiliary bar, matching where tools like this one dock by
default). The panel shows an append-only, top-to-bottom scrolling history above a multi-line
prompt textbox: typing text and pressing Enter appends it as a new entry at the bottom of the
history and clears the box; Shift+Enter inserts a newline in the box instead of submitting.
Today the panel only echoes what the user typed into its own history — it does not
yet talk to a klorb process. See [[klorb-server]] for the JSONL stdin/stdout protocol a later
iteration of this extension is expected to drive.

## How it works

* `vscode-plugin/src/extension.ts` is the extension's activation entry point
  (`package.json`'s `main`, compiled to `out/extension.js`). `activate()` constructs one
  `KlorbSessionViewProvider` and registers it as the provider for the `klorb.sessionView`
  webview view, and registers the `klorb.restartSession` command.
* `vscode-plugin/src/klorbSessionViewProvider.ts`'s `KlorbSessionViewProvider` implements
  `vscode.WebviewViewProvider`. `resolveWebviewView()` enables scripts, restricts
  `localResourceRoots` to the extension's own install directory, and sets the webview's HTML.
  `restart()` re-sets the webview's HTML (with a fresh nonce and a cache-busting query string
  on the compiled webview script's URI), which is what the `klorb.restartSession` command
  palette entry calls — it reloads the panel's webview document (and therefore
  `out/webview/main.js`) without requiring a full "Reload Window", so a rebuilt webview script
  is picked up immediately. This only covers changes to `src/webview/*`, though: `restart()`
  itself runs as a method on the already-`require()`d `KlorbSessionViewProvider` instance, so a
  change to `klorbSessionViewProvider.ts` or `extension.ts` needs VS Code's own "Developer:
  Reload Window" (or a full restart) to take effect, the same as for any other extension host
  code change. `registerWebviewViewProvider()` is called with
  `webviewOptions: { retainContextWhenHidden: true }` so the in-progress history and draft
  text survive the view being hidden (e.g. the auxiliary bar closed) and re-shown.
* Panel placement comes from `package.json`'s `contributes.viewsContainers.secondarySidebar`
  entry (container id `klorb`) plus a `views.klorb` entry (view id `klorb.sessionView`, type
  `webview`) — `secondarySidebar` is the manifest key for docking a container to the secondary
  side bar (VS Code's internal `ViewContainerLocation.AuxiliaryBar`) by default. The secondary
  side bar itself is still closed by default in a fresh window regardless of what's docked
  there; a user opens it via View > Appearance > Secondary Side Bar or the `Ctrl+Alt+B` /
  `Cmd+Option+B` keybinding, the same as opening it for any other extension's view.
* The webview's own HTML document (built in `KlorbSessionViewProvider._getHtml()`) is a
  near-empty shell: just a `<div id="root">` and the bundled script tag. Everything visible —
  the `.title` div reading "Klorb session", the `#history` scrollback, and the `.input-row`
  holding `#prompt-input`/`#submit-button` — is rendered into `#root` by React, not written
  into the HTML string. `vscode-plugin/media/main.css` still styles those elements by id/class
  against the VS Code theme's CSS custom properties (`--vscode-*`) exactly as before, since
  React produces the same DOM shape; `#root { display: contents }` keeps the mount div itself
  out of the flex layout so `.title`/`#history`/`.input-row` lay out as if they were direct
  children of `<body>`. `#history` is the only element with `overflow-y: auto`, so a scrollbar
  appears there once its content overflows the panel.
* `vscode-plugin/src/webview/main.tsx` is the webview's entry point, compiled separately (see
  "Webview build" below) and loaded as a plain classic `<script>`. `main()` calls
  `acquireVsCodeApi()` exactly once, reads any persisted `SessionState` via
  `vscode.getState()`, and mounts `<App vscode={vscode} initialEntries={state.entries} />` into
  `#root` with `react-dom/client`'s `createRoot()`. Calling `acquireVsCodeApi()` a second time
  anywhere (in `main.tsx` or `App.tsx`) throws and silently aborts whatever called it — the VS
  Code webview API only allows one call per page load — which is why the single `vscode` value
  from that one call is threaded through as a prop rather than re-acquired.
* `vscode-plugin/src/webview/App.tsx`'s `App` component owns all of the panel's interactive
  state: `entries` (the history, seeded from `initialEntries`) and `draft` (the textarea's
  controlled value). Submitting appends `draft.trim()` to `entries` and clears `draft`; a
  `useEffect` keyed on `entries` calls `vscode.setState({ entries })` (so history survives
  `retainContextWhenHidden`'s context teardown/rebuild) and scrolls the history's last child
  into view. History entries are keyed by array index in the `.map()` that renders them — safe
  here specifically because entries only ever append, never reorder or get removed or inserted
  in the middle, which is the one case React's own docs call out as fine for index keys. The
  one piece of logic broken out into its own pure function, independent of React, is
  `vscode-plugin/src/webview/keyHandling.ts`'s `classifyEnterKey(shiftKey, ctrlKey)`, which
  returns `'newline'` if either modifier is held and `'submit'` otherwise; `App`'s `onKeyDown`
  handler calls `event.preventDefault()` and submits only when it returns `'submit'`, otherwise
  it lets the textarea's own default newline insertion happen. Pulling this one decision out as
  a standalone function is what makes it reachable from
  `vscode-plugin/test/keyHandling.test.ts` without a browser, React, or a VS Code extension
  host.

### Webview build: esbuild bundle, not an ES module

The extension host code (`src/extension.ts`, `src/klorbSessionViewProvider.ts`) and the
webview code (`src/webview/*.ts`/`*.tsx`) run in two different JavaScript environments — the
extension host is a Node/CommonJS process with the `vscode` module available, the webview is a
sandboxed `vscode-webview://` document with neither — so they're built by two different
pipelines:

* `tsconfig.json` compiles everything under `src/` *except* `src/webview/`, with
  `module`/`moduleResolution` set to `nodenext` (CommonJS output, resolvable by the extension
  host's `require()` — the "next" name tracks whatever Node's current `package.json`
  `exports`-aware resolution algorithm is; it is not a floor pinning the extension to Node 16,
  just TypeScript's naming for that resolution algorithm), into `out/`.
* `tsconfig.webview.json` type-checks `src/webview/*.ts`/`*.tsx` (`jsx: "react-jsx"` for the
  automatic JSX runtime — no `import React` needed in `App.tsx`; `moduleResolution: "bundler"`,
  the mode meant for exactly this situation, a tool like esbuild that bundles ESM without any
  of it ever running through a real Node `require()`; `types: []` so ambient `@types/node`
  globals aren't pulled into browser-only code) but does not emit (`noEmit: true`) — `esbuild`
  does, bundling `src/webview/main.tsx` (`--bundle --sourcemap
  --define:process.env.NODE_ENV="development" --format=iife --platform=browser
  --target=es2022 --tsconfig=tsconfig.webview.json -o out/webview/main.js`) into one
  self-contained file with React and `react-dom/client` inlined alongside `App.tsx`'s and
  `keyHandling.ts`'s own code — no runtime `import`/`export` statement is left anywhere in the
  output. The `--define` is required regardless of which value it's given — React's own source
  reads `process.env.NODE_ENV` directly, and a plain browser webview has no `process` global,
  so an undefined reference there throws at runtime — but the *value* is deliberately
  `"development"`, not `"production"`, and `--minify` is deliberately omitted: `make
  install`/`compile` is the local dev loop today (nothing here ships to real users yet), so the
  build favors debuggability over size. A production build (`--minify
  --define:process.env.NODE_ENV="production"`) strips React's own dev-mode warnings and
  replaces its error text with a minified code, which cuts this stub's bundle from 1.1MB to
  190KB but actively worked against debugging this exact webview during development — see
  `docs/adrs/use-react-for-the-webview-ui.md` for the measurements and the reasoning to defer a
  production build mode until this extension actually needs to be distributed. The webview HTML
  loads the bundle as a plain `<script nonce="...">`, not `<script type="module">`. See
  `docs/adrs/bundle-webview-script-with-esbuild-not-es-modules.md` for why a module script
  isn't used here: a nonce on a `<script type="module">` element does not by itself extend
  trust to that module's statically-imported dependencies under the page's CSP, so a prior,
  non-bundled version of `main.js`'s `import` of `keyHandling.js` was silently blocked and the
  whole script failed to run.

`npm run compile` (and the Makefile's `compile` target) runs the extension-host `tsc`, the
webview `tsc --noEmit` type-check, and the `esbuild` bundle, in that order. `vitest` (`make
test`) imports `src/webview/keyHandling.ts` directly rather than the built bundle, since Vitest
transpiles TypeScript itself independent of either build path.

## Build tooling

`vscode-plugin/Makefile` mirrors `klorb/Makefile`'s target names, mapped onto the npm/VS Code
toolchain in place of `pip`/`uv`:

* `sync_deps` runs `npm install`, resolving `package.json`'s version ranges into
  `package-lock.json` — the npm analog of `uv pip compile` recomputing
  `dev-requirements.txt`/`release-requirements.txt`.
* `install_deps` (`npm ci --omit=dev`) and `install_dev_deps` (`npm ci`) install exactly what's
  pinned in `package-lock.json`, matching `klorb/Makefile`'s split between a runtime-only
  install and one that also brings in lint/typecheck/test tooling. The extension has no
  runtime dependencies of its own today (only `devDependencies`), so `install_deps` installs
  nothing — that's expected, not a bug. `react`/`react-dom` are `devDependencies` too, despite
  ending up in the shipped `.vsix`: nothing in the packaged extension ever `require()`s them at
  runtime — `esbuild` inlines them into `out/webview/main.js` at build time — so they belong
  with the other build-time-only tooling (`typescript`, `esbuild`, `eslint`) rather than as a
  `dependencies` entry that would make `vsce` (or a plain `npm ci --omit=dev`) try to ship or
  install a separate `node_modules/react` alongside the bundle that already contains it.
* `lint` runs `eslint` (flat config in `eslint.config.mjs`, `typescript-eslint`'s recommended
  rules plus `eslint-plugin-react-hooks`'s `recommended-latest` config scoped to
  `src/webview/**/*.tsx`) over `src/` and `test/`.
* `test` runs `vitest run` over `test/`.
* `compile` runs the extension-host `tsc`, the webview `tsc --noEmit` type-check, and the
  `esbuild` webview bundle described above.
* `install` (not present in `klorb/Makefile`, since the Python side has no editor-installation
  step) runs `compile`, packages the result into a `.vsix` with `@vscode/vsce`, and installs
  it into the local VS Code with `code --install-extension` — the interop step needed to
  actually try the extension out, as opposed to just linting/testing it.
* `clean` removes `out/`, `coverage/`, the packaged `.vsix`, and `tsconfig.tsbuildinfo`.
  `distclean` additionally removes `node_modules/`.

## Out of scope

* No communication with a running `klorb` process yet. `[[klorb-server]]` documents the JSONL
  protocol a later iteration of `KlorbSessionViewProvider` is expected to speak (spawning
  `klorb server` as a child process and exchanging JSONL messages over its stdin/stdout)
  instead of only appending typed text to its own history.
* History entries are plain text nodes with no formatting, editing, deletion, or persistence
  beyond `vscode.getState()`'s in-memory-while-the-window-is-open lifetime — nothing is written
  to disk.
* No extension settings/configuration surface exists yet (no `contributes.configuration`).
