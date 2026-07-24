# VS Code plugin

## Summary

`vscode-plugin/` is a VS Code extension that docks a "Klorb session" panel in the editor's
secondary side bar (the right-hand auxiliary bar, matching where tools like this one dock by
default). The panel shows an append-only, top-to-bottom scrolling history of prompts and
streamed agent output above a multi-line prompt textbox. The extension speaks the [Agent
Client Protocol](https://agentclientprotocol.com) (ACP) — see [[klorb-server]] — to a spawned
`klorb server` child process: submitting a prompt sends `session/prompt`, and the server's
streamed response and thinking text (`session/update` → `agent_message_chunk`/
`agent_thought_chunk`) render live in the history as markdown/a collapsed disclosure
respectively. A Stop button (or Escape while a turn is in flight) sends `session/cancel`.
Shift+Enter (or Ctrl+Enter) inserts a newline in the prompt box instead of submitting.
Activation spawns one `klorb server` process and creates one ACP session, shared by the whole
extension; **Klorb: Restart Server** kills and respawns the child and re-initializes the ACP
connection (picking up any change to the `klorb.serverPath`/`klorb.openRouterApiKey`/
`klorb.configPath` settings — see "Configuration" below); **Klorb: Restart Session** replaces
the live ACP session with a fresh one (`session/new`) and clears the panel's history, without
restarting the child process.

## How it works

* `vscode-plugin/src/extension.ts` is the extension's activation entry point
  (`package.json`'s `main`, compiled to `out/extension.js`). `activate()` constructs one
  `KlorbServerProcess`, one `KlorbSessionViewProvider`, and one `AcpConnection` wired to both
  (the provider is the connection's `SessionUpdateListener`; `provider.setConnection()`
  completes the reference cycle after construction, since the provider and connection
  otherwise have no way to point at each other from a single constructor call). It starts the
  connection via `readServerOptions()` (reads `klorb.serverPath`, `klorb.openRouterApiKey`,
  and `klorb.configPath` off `vscode.workspace.getConfiguration('klorb')`, folding the API key
  into the child's `OPENROUTER_API_KEY` environment variable when non-empty) and `sessionCwd()`
  (the first workspace folder, or the home directory if none is open — ACP requires an
  absolute `cwd` for `session/new`), registers the view provider for the `klorb.sessionView`
  webview view, and registers `klorb.restartSession` (calls `AcpConnection.newSession()` for a
  fresh conversation, then posts a `sessionReset` message to clear the panel) and
  `klorb.restartServer` (calls `AcpConnection.start()` again — `start()` stops any prior child
  first, so this both applies changed settings and recovers from a crashed/hung server).
  `context.subscriptions` stops the connection (killing the child process) when the extension
  deactivates. A handshake or connection failure at any of these points surfaces as both a VS
  Code error notification and a `turnError` panel message, rather than failing silently.
* `vscode-plugin/src/klorbServerProcess.ts`'s `KlorbServerProcess` owns spawning, killing, and
  restarting the one `klorb server` child process — nothing about the wire protocol spoken over
  its stdio. `start()` stops any running child, spawns `<command> server` (appending `--config
  <configPath>` when non-empty) via an injected `SpawnFn` (defaulting to real
  `child_process.spawn`, so tests can drive the class against a fake process), and returns the
  new `ChildProcessWithoutNullStreams` so the caller can bind a protocol connection to its
  stdio. `stop()` kills the child if one is running.
* `vscode-plugin/src/acpConnection.ts`'s `AcpConnection` owns the ACP client-side connection to
  the child: `start(options, cwd)` spawns the child via `KlorbServerProcess`, builds an
  `acp.ndJsonStream()` over its stdout/stdin (via Node's `Readable.toWeb()`/`Writable.toWeb()`,
  bridging the child's Node streams to the Web Streams API the SDK's stream type expects), and
  constructs the SDK's `ClientSideConnection` with a `KlorbAcpClient` as its `Client`
  implementation. It performs `initialize()` (asserting the negotiated `protocolVersion`
  matches `acp.PROTOCOL_VERSION`; a mismatch or a hung/failed handshake — bounded by a 10-second
  timeout — throws a readable error naming `klorb.serverPath` as the likely fix, covering an old
  pre-ACP klorb binary) and then `newSession(cwd)`, storing the returned `sessionId`.
  `prompt(text)` sends `session/prompt` with one `TextContentBlock` and resolves with the ACP
  `stopReason`; only one prompt may be in flight at a time (matching [[klorb-server]]'s own
  one-prompt-at-a-time rule), so a second call while one is running rejects immediately rather
  than queuing. `cancel()` sends `session/cancel` as a fire-and-forget notification — the
  in-flight `prompt()` call still resolves normally once the server winds the turn down and
  replies with `stopReason: "cancelled"`. `stop()` kills the child and rejects any in-flight
  `prompt()` with a restart-style error; the same rejection fires automatically if the
  connection closes out from under an in-flight prompt (child crash, unexpected EOF).
  `errorMessage()` (exported alongside the class) renders both real `Error` instances and the
  SDK's plain `{code, message}` JSON-RPC rejection objects as a readable string, since ACP
  request failures reject with the latter shape, not an `Error`.
* `vscode-plugin/src/klorbAcpClient.ts`'s `KlorbAcpClient` implements the ACP SDK's `Client`
  interface: the handler for requests/notifications the server sends back over the connection.
  `sessionUpdate()` dispatches `agent_message_chunk`/`agent_thought_chunk` text content to a
  `SessionUpdateListener` (`onAgentText`/`onThoughtText`) and logs (rather than errors on) any
  other update kind, since later increments add handling for `tool_call`, `plan`, etc.
  `requestPermission()` auto-answers every ask with the first `reject_once` (falling back to
  `reject_always`, then whatever option is first) — a stand-in until `plan-016-006` builds the
  real approval panel — logging what was declined so a denied tool call is visible in the
  extension's output channel rather than silently vanishing. `readTextFile()`/
  `writeTextFile()`/`createTerminal()` throw the SDK's `RequestError.methodNotFound()`
  synchronously, matching the client never advertising the `fs`/`terminal` capabilities during
  `initialize()`.
* `vscode-plugin/src/klorbSessionViewProvider.ts`'s `KlorbSessionViewProvider` implements
  `vscode.WebviewViewProvider` and `SessionUpdateListener`. `resolveWebviewView()` enables
  scripts, restricts `localResourceRoots` to the extension's own install directory, sets the
  webview's HTML, and registers `onDidReceiveMessage` to parse and dispatch `WebviewMessage`s
  (see "Webview message protocol" below). `onAgentText`/`onThoughtText` (the
  `SessionUpdateListener` methods `AcpConnection` calls as chunks stream in) post `agentChunk`/
  `thoughtChunk` host messages. `_runTurn(text)` — invoked for a `submitPrompt` message — posts
  `turnStarted`, awaits `AcpConnection.prompt(text)`, and posts either `turnEnded {stopReason}`
  or (on rejection) `turnError {message}`; a `cancelTurn` message calls
  `AcpConnection.cancel()` directly, with no reply of its own (the in-flight prompt's own
  `turnEnded`/`turnError` follow-up is the confirmation). `restart()` re-sets the webview's HTML
  (with a fresh nonce and a cache-busting query string on the compiled webview script's URI),
  which is what `klorb.restartSession` calls — it reloads the panel's webview document (and
  therefore `out/webview/main.js`) without requiring a full "Reload Window", so a rebuilt
  webview script is picked up immediately. This only covers changes to `src/webview/*`, though:
  `restart()` itself runs as a method on the already-`require()`d `KlorbSessionViewProvider`
  instance, so a change to `klorbSessionViewProvider.ts`, `acpConnection.ts`, or `extension.ts`
  needs VS Code's own "Developer: Reload Window" (or a full restart) to take effect, the same as
  for any other extension host code change. `registerWebviewViewProvider()` is called with
  `webviewOptions: { retainContextWhenHidden: true }` so the in-progress history and draft text
  survive the view being hidden (e.g. the auxiliary bar closed) and re-shown.
* Panel placement comes from `package.json`'s `contributes.viewsContainers.secondarySidebar`
  entry (container id `klorb`) plus a `views.klorb` entry (view id `klorb.sessionView`, type
  `webview`) — `secondarySidebar` is the manifest key for docking a container to the secondary
  side bar (VS Code's internal `ViewContainerLocation.AuxiliaryBar`) by default. The secondary
  side bar itself is still closed by default in a fresh window regardless of what's docked
  there; a user opens it via View > Appearance > Secondary Side Bar or the `Ctrl+Alt+B` /
  `Cmd+Option+B` keybinding, the same as opening it for any other extension's view.
* The webview's own HTML document (built in `KlorbSessionViewProvider._getHtml()`) is a
  near-empty shell: just a `<div id="root">` and the bundled script tag. Everything visible is
  rendered into `#root` by React. `vscode-plugin/media/main.css` styles the rendered DOM by
  id/class against the VS Code theme's CSS custom properties (`--vscode-*`); `#root { display:
  contents }` keeps the mount div itself out of the flex layout so `.title`/`#history`/
  `.input-row` lay out as if they were direct children of `<body>`. `#history` is a flex column
  (the only element with `overflow-y: auto`, so a scrollbar appears there once its content
  overflows the panel).

### Webview UI structure

* `vscode-plugin/src/webview/main.tsx` is the webview's entry point, compiled separately (see
  "Webview build" below) and loaded as a plain classic `<script>`. It imports the
  `@vscode-elements/elements` custom-element modules used by the panel (registering
  `<vscode-textarea>`/`<vscode-button>` with the browser), calls `acquireVsCodeApi()` exactly
  once, reads any persisted `SessionState` via `vscode.getState()`, and mounts `<App vscode=
  {vscode} initialEntries={state.entries} />` into `#root` with `react-dom/client`'s
  `createRoot()`. Calling `acquireVsCodeApi()` a second time anywhere throws and silently
  aborts whatever called it — the VS Code webview API only allows one call per page load —
  which is why the single `vscode` value from that one call is threaded through as a prop
  rather than re-acquired (see `docs/adrs/call-acquirevscodeapi-exactly-once-per-webview-page.md`).
* `vscode-plugin/src/webview/App.tsx`'s `App` component is the panel's layout shell, top to
  bottom: the title, `HistoryView`, a placeholder `#interaction-area` div (approval/question
  panels mount there starting in `plan-016-006`), `PromptInput`, and a placeholder
  `#status-row` div (model/thinking/permission-mode controls arrive in `plan-016-009`). It owns
  all interactive state: `entries` (a `HistoryEntry[]`, seeded from `initialEntries`) and
  `inFlight` (whether a turn is currently running). A `window` `message` listener (subscribed
  for the panel's lifetime via a `useEffect` with an empty dependency array) parses each
  incoming payload with `parseHostMessage()` and applies it to both pieces of state via the
  pure functions in `historyModel.ts`. Submitting a prompt appends a `'prompt'` entry
  optimistically, sets `inFlight` to `true` (the host's own `turnStarted`/`turnError` follow-up
  confirms or corrects it), and posts `{type: 'submitPrompt', text}`. A separate `useEffect`
  keyed on `entries` calls `vscode.setState({entries})` (so history survives
  `retainContextWhenHidden`'s context teardown/rebuild) and scrolls the history's last child
  into view.
* `vscode-plugin/src/webview/historyModel.ts` is the pure, React-independent reducer logic for
  the history list, kept separate specifically so it's unit-testable without mounting anything:
  * `HistoryEntry` is `{kind: 'prompt' | 'response' | 'thinking' | 'error' | 'notice', text,
    streaming: boolean}`.
  * `appendPrompt(entries, text)` appends a finished `'prompt'` entry.
  * `applyHostMessage(entries, message)` is the `HostMessage` reducer: `agentChunk`/
    `thoughtChunk` extend the trailing streaming entry of the matching kind, or start a new one
    if the last entry is a different kind (or not currently streaming) — so thinking and
    response phases interleave correctly across a turn. `turnEnded` finalizes every streaming
    entry's `streaming` flag and, for any `stopReason` other than `"end_turn"`, appends a
    `'notice'` entry naming the reason. `turnError` finalizes streaming entries and appends an
    `'error'` entry. `sessionReset` clears the list.
  * `applyTurnFlag(inFlight, message)` is the parallel reducer for the `inFlight` boolean:
    `turnStarted` raises it, `turnEnded`/`turnError`/`sessionReset` clear it, every other
    message leaves it unchanged.
* `vscode-plugin/src/webview/components/HistoryView.tsx` renders the `HistoryEntry[]` list:
  `'prompt'` entries as a right-aligned `.bubble` (index-keyed — safe here specifically because
  entries only ever append, never reorder or get removed or inserted in the middle, the one
  case React's own docs call out as fine for index keys); `'response'` entries through
  `react-markdown`; `'thinking'` entries as a collapsed-by-default `<details>` disclosure
  (muted/italic styling, matching the TUI's thinking block) that keeps streaming into its body
  while the reader has it open; `'error'`/`'notice'` entries as plain styled text.
* `vscode-plugin/src/webview/components/PromptInput.tsx` renders the `<vscode-textarea>` +
  `<vscode-button>` input row: disabled (and the button replaced by a Stop button) while
  `inFlight` is true. Its own `onKeyDown` handles Escape (calls `onCancel` when `inFlight`) and
  Enter, delegating the submit-vs-newline decision to `keyHandling.ts`'s `classifyEnterKey()`.
* `vscode-plugin/src/webview/keyHandling.ts`'s `classifyEnterKey(shiftKey, ctrlKey)` returns
  `'newline'` if either modifier is held and `'submit'` otherwise. Pulling this one decision out
  as a standalone function is what makes it reachable from `vscode-plugin/test/keyHandling.test.ts`
  without a browser, React, or a VS Code extension host.

### Webview message protocol

The webview and the extension host exchange messages shaped by the discriminated unions in
`vscode-plugin/src/shared/webviewMessages.ts` — one module included by both tsconfigs (host
and webview) so the same types check both sides — over the standard `vscode.postMessage()` /
`window.addEventListener('message', ...)` webview messaging channel. The webview never speaks
ACP directly; `KlorbSessionViewProvider` is the only place that translates between the two (see
`docs/adrs/vscode-webview-stays-acp-ignorant-behind-typed-messages.md`).

* Webview → host (`WebviewMessage`): `{type: 'submitPrompt', text: string}` (once per submitted
  prompt) and `{type: 'cancelTurn'}` (Stop button or Escape while a turn is running).
* Host → webview (`HostMessage`): `{type: 'turnStarted'}`, `{type: 'agentChunk', text:
  string}`, `{type: 'thoughtChunk', text: string}`, `{type: 'turnEnded', stopReason: string}`,
  `{type: 'turnError', message: string}`, `{type: 'sessionReset'}`.
* `parseHostMessage()`/`parseWebviewMessage()` are the type guards each side runs on every
  incoming payload before acting on it, since both `onDidReceiveMessage`'s argument (host side)
  and `MessageEvent.data` (webview side) are untyped `unknown`.

### Webview build: esbuild bundle, not an ES module

The extension host code (`src/extension.ts`, `src/acpConnection.ts`,
`src/klorbSessionViewProvider.ts`, ...) and the webview code (`src/webview/*.ts`/`*.tsx`,
`src/shared/*.ts`) run in two different JavaScript environments — the extension host is a
Node/CommonJS process with the `vscode` module and Node's `child_process`/`stream` APIs
available, the webview is a sandboxed `vscode-webview://` document with neither — so they're
built by two different pipelines:

* `tsconfig.json` compiles everything under `src/` *except* `src/webview/`, with
  `module`/`moduleResolution` set to `nodenext` (CommonJS output, resolvable by the extension
  host's `require()`), into `out/`. `@agentclientprotocol/sdk` is ESM-only, so
  `AcpConnection.start()` loads it with a dynamic `import()` rather than a top-level import —
  the only way a CommonJS module can consume a pure-ESM package; every other symbol the host
  imports from the SDK is type-only (erased at compile time, so it doesn't hit this
  restriction). `skipLibCheck: true` is set because the SDK's own shipped `.d.ts` re-exports its
  generated schema module in a way `tsc` flags as an export-ambiguity error under this
  project's strict settings — a problem in the SDK's own type declarations, not in code this
  project controls, so it's skipped rather than routing every SDK type through a local
  re-export shim.
* `tsconfig.webview.json` type-checks `src/webview/*.ts`/`*.tsx` and `src/shared/*.ts`
  (`jsx: "react-jsx"` for the automatic JSX runtime; `moduleResolution: "bundler"`;
  `types: []` so ambient `@types/node` globals aren't pulled into browser-only code;
  `skipLibCheck: true` for the same SDK-declaration reason as above, though the webview
  tsconfig doesn't import the SDK itself) but does not emit (`noEmit: true`) — `esbuild` does,
  bundling `src/webview/main.tsx` into one self-contained `out/webview/main.js` with React,
  `react-dom/client`, `react-markdown`, and `@vscode-elements/elements` all inlined alongside
  the plugin's own webview code. It also includes `vscode-plugin/types/*.d.ts` — the vendored
  vscode-elements JSX declarations (see "Component library" below) — so `<vscode-button>` etc.
  type-check as JSX intrinsics. The `--define:process.env.NODE_ENV=\"development\"` flag and
  the choice to skip `--minify` are unchanged from the original stub (see
  `docs/adrs/use-react-for-the-webview-ui.md`); `docs/adrs/bundle-webview-script-with-esbuild-not-es-modules.md`
  explains why the output loads as a plain `<script nonce="...">` rather than
  `<script type="module">`.

`npm run compile` (and the Makefile's `compile` target) runs the extension-host `tsc`, the
webview `tsc --noEmit` type-check, and the `esbuild` bundle, in that order. `vitest` (`make
test`) transpiles TypeScript itself (via `vitest.config.ts`'s `esbuild.jsx: 'automatic'`
setting, matching `tsconfig.webview.json`'s JSX mode) independent of either build path, so it
imports source modules directly rather than the built bundle.

### Component library

The webview's interactive controls (`<vscode-textarea>`, `<vscode-button>`, and later
increments' option grids, selects, and collapsibles) are `@vscode-elements/elements` custom
elements, rendered directly from React 19 JSX with no wrapper package — React 19 passes JSX
props straight through to custom elements' properties/attributes. Their TypeScript JSX typings
live in `vscode-plugin/types/global.d.ts`, vendored from the vscode-elements examples repo's
own `global.d.ts` (declaring the `react`-module `JSX.IntrinsicElements` additions for every
element the library ships) rather than hand-written per element as they're adopted. See
`docs/adrs/use-vscode-elements-for-webview-controls.md` for why this library over the
alternatives.

Markdown responses render via `react-markdown`, chosen over `marked` + `innerHTML` because it
renders to React elements without `dangerouslySetInnerHTML` — relevant since the rendered text
is model-generated and the webview runs under a CSP-locked `vscode-webview://` origin.

## Build tooling

`vscode-plugin/Makefile` mirrors `klorb/Makefile`'s target names, mapped onto the npm/VS Code
toolchain in place of `pip`/`uv`:

* `sync_deps` runs `npm install`, resolving `package.json`'s version ranges into
  `package-lock.json` — the npm analog of `uv pip compile` recomputing
  `dev-requirements.txt`/`release-requirements.txt`.
* `install_deps` (`npm ci --omit=dev`) and `install_dev_deps` (`npm ci`) install exactly what's
  pinned in `package-lock.json`, matching `klorb/Makefile`'s split between a runtime-only
  install and one that also brings in lint/typecheck/test tooling. `package.json`'s
  `dependencies` are `@agentclientprotocol/sdk` (the extension host `require()`s/`import()`s it
  at runtime, unlike the bundled webview deps — the plugin's first true runtime dependency),
  `@vscode-elements/elements`, `react`, `react-dom`, and `react-markdown` — the last four are
  runtime dependencies of the *webview*, not the host, but `esbuild` inlines them into
  `out/webview/main.js` at build time rather than the packaged extension `require()`-ing a
  separate `node_modules` copy at runtime, so from `vsce`/`npm ci`'s point of view they still
  need to be present wherever `install` (below) runs its build, i.e. wherever `install_deps` or
  `install_dev_deps` ran. `devDependencies` covers everything build/lint/test-only
  (`typescript`, `esbuild`, `eslint`, `vitest`, `jsdom`, `@testing-library/react`, ...).
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

## Configuration

`package.json`'s `contributes.configuration` (title "Klorb") declares three settings, all read by
`extension.ts`'s `readServerOptions()` each time a connection is started (at activation and on
`klorb.restartServer`):

* `klorb.serverPath` (string, default `"klorb"`): the command run as `<serverPath> server` to
  launch the child process. Overriding it points the extension at a `klorb` not on `PATH` (e.g.
  a venv's `bin/klorb` during local development of the Python side).
* `klorb.openRouterApiKey` (string, default `""`): forwarded to the child process as its
  `OPENROUTER_API_KEY` environment variable (see `klorb.openrouter.OPENROUTER_API_KEY_ENV_VAR` in
  the `klorb/` subproject) when non-empty; left out of the child's environment entirely when
  empty, so an API key already exported in the shell that launched VS Code still reaches the
  child unless this setting overrides it. This is a plain string setting, not VS Code
  `SecretStorage` — it is stored (and, if the user syncs settings, synced) as plaintext in
  `settings.json` like any other setting.
* `klorb.configPath` (string, default `""`): path to an additional `klorb-config.json` file,
  passed to the child process as `<serverPath> server --config <configPath>` when non-empty (see
  [[klorb-server]]'s `--config` flag). Left off the spawn args entirely when empty.

## Try it

1. `make -C vscode-plugin install` (packages a `.vsix` and installs it into the local VS Code —
   run once, or again after a source change to pick it up).
2. Open the Klorb view: View > Appearance > Secondary Side Bar (or `Ctrl+Alt+B` /
   `Cmd+Option+B`), then select the Klorb icon in the auxiliary bar.
3. Type a prompt and press Enter. Thinking (if the configured model streams it) and the
   response render live in the history; press Stop (or Escape) mid-stream to cancel and observe
   the turn end with a "Turn ended: cancelled" notice.
4. Run **Klorb: Restart Session** from the command palette to clear the panel and start a fresh
   ACP session without restarting the child process; run **Klorb: Restart Server** to kill and
   respawn `klorb server` itself (e.g. after changing `klorb.serverPath`).

## Out of scope

* No tool-call display, permission approval UI, `AskUserQuestions` panel, task panel, or
  model/thinking/permission-mode controls yet — every permission ask is auto-rejected by
  `KlorbAcpClient.requestPermission()`, since [[klorb-server]] itself fails every `"ask"`
  permission verdict closed at this checkpoint anyway (no `on_permission_ask` callback wired
  server-side until `plan-016-005`). These land across `plan-016-004` through `plan-016-011`.
* No mid-turn message queueing — a second `submitPrompt` while a turn is in flight is rejected
  by `AcpConnection.prompt()` (mirroring [[klorb-server]]'s own one-prompt-at-a-time rule), not
  queued client-side. Lands in `plan-016-012`.
* No persistence beyond `vscode.getState()`'s in-memory-while-the-window-is-open lifetime —
  nothing is written to disk, and history is lost on `klorb.restartServer`, `klorb.
  restartSession`, or a full window reload.
* No production (minified) webview bundle — see
  `docs/adrs/bundle-webview-script-with-esbuild-not-es-modules.md`'s reasoning, unchanged from
  the original stub.
