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
* The webview's own document is a static HTML shell (built in
  `KlorbSessionViewProvider._getHtml()`): a `.title` div reading "Klorb session", a `#history`
  div the script appends entries into, and an `.input-row` holding the `#prompt-input`
  textarea and `#submit-button` button. `vscode-plugin/media/main.css` styles it against the
  VS Code theme's CSS custom properties (`--vscode-*`) so it matches light/dark/high-contrast
  themes automatically; `#history` is the only element with `overflow-y: auto`, so a scrollbar
  appears there once its content overflows the panel.
* `vscode-plugin/src/webview/main.ts` is the webview's own script, compiled separately (see
  "Webview build: esbuild bundle, not an ES module" below) and loaded as a plain classic
  `<script>`. `main()` calls `acquireVsCodeApi()` exactly once and threads the result through
  to every function that needs it (`submit()`'s `vscode.setState()` call, the initial
  `vscode.getState()` read) — the VS Code webview API throws if `acquireVsCodeApi()` is called
  a second time in the same page load, which silently aborts the whole script (including every
  `addEventListener()` call still below the throwing line) since nothing here catches it.
  `submit()` reads and trims the textarea, appends a `.history-entry` div to `#history`,
  scrolls it into view, and clears the textarea; entries are also pushed into
  `vscode.getState()`/`setState()` so they survive `retainContextWhenHidden`'s context
  teardown/rebuild. The one piece of logic broken out into its own pure function is
  `vscode-plugin/src/webview/keyHandling.ts`'s
  `classifyEnterKey(shiftKey, ctrlKey)`, which returns `'newline'` if either modifier is held
  and `'submit'` otherwise; `main.ts`'s `keydown` listener calls `event.preventDefault()` and
  submits only when it returns `'submit'`, otherwise it lets the textarea's own default newline
  insertion happen. Pulling this one decision out as a standalone function is what makes it
  reachable from `vscode-plugin/test/keyHandling.test.ts` without a browser or a VS Code
  extension host.

### Webview build: esbuild bundle, not an ES module

The extension host code (`src/extension.ts`, `src/klorbSessionViewProvider.ts`) and the
webview code (`src/webview/*.ts`) run in two different JavaScript environments — the extension
host is a Node/CommonJS process with the `vscode` module available, the webview is a sandboxed
`vscode-webview://` document with neither — so they're built by two different pipelines:

* `tsconfig.json` compiles everything under `src/` *except* `src/webview/`, with
  `module`/`moduleResolution` set to `node16` (CommonJS output, resolvable by the extension
  host's `require()`), into `out/`.
* `tsconfig.webview.json` type-checks `src/webview/*.ts` (`noEmit: true`, `types: []` so
  ambient `@types/node` globals aren't pulled into browser-only code) but does not emit —
  `esbuild` (`src/webview/main.ts --bundle --format=iife --platform=browser --target=es2022 -o
  out/webview/main.js`) does, inlining `keyHandling.ts`'s exported `classifyEnterKey()` directly
  into the same file rather than leaving a runtime `import` between them. `main.ts`'s own
  TypeScript source still `import`s `classifyEnterKey` from `keyHandling.ts` — reusing the one
  function rather than duplicating its logic for the webview — but esbuild resolves that import
  at build time, and `out/webview/main.js` itself has no `import`/`export` statement left in it.
  The webview HTML loads the bundle as a plain `<script nonce="...">`, not `<script
  type="module">`. See `docs/adrs/bundle-webview-script-with-esbuild-not-es-modules.md` for why
  a module script isn't used here: a nonce on a `<script type="module">` element does not by
  itself extend trust to that module's statically-imported dependencies under the page's CSP,
  so `main.js`'s `import` of `keyHandling.js` was silently blocked and the whole script failed
  to run.

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
  nothing — that's expected, not a bug.
* `lint` runs `eslint` (flat config in `eslint.config.mjs`, `typescript-eslint`'s recommended
  rules) over `src/` and `test/`.
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
