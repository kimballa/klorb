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
respectively. Tool calls (`session/update` → `tool_call`/`tool_call_update`) render as chips
that go from a busy spinner to a one-line completed/failed summary, expandable to full detail
or a colored diff view; a chip naming a file location links to opening that file (or a real
diff editor) in VS Code itself. A permission ask or `EscalatePrivileges` request
(`session/request_permission`) renders as an `ApprovalPanel` docked directly above the prompt
input — an option grid, a bash command preview with a "show full command" disclosure, a
grant-pattern/risk-level summary, and a free-text "Other…" redirect — with the answered
decision recorded as a compact permanent entry in the history scroll; an `AskUserQuestions`
question (`_klorb/askUserQuestions`) renders in the same interaction area as a `QuestionPanel` —
header chip, "Question N of M" caption, an option list, and a free-text "Other…" row — Escape
cancelling the whole question batch, not just the current question; the tool-call-limit
extension ask (`_klorb/raiseToolCallLimit`) instead surfaces as a native modal warning. A Stop
button (or Escape while a turn is in flight) sends `session/cancel`; a cancelled turn's
still-streaming response/thinking entry gets an "(interrupted)" marker rather than just quietly
stopping. Submitting while a turn is already running — when the server advertises
`_klorb/enqueueMessage` — queues the message into the running turn instead of being rejected,
rendered in italic "Queued message" styling until the server confirms delivery; without that
capability the input stays disabled for the turn's duration, as it always has.
Shift+Enter (or Ctrl+Enter) inserts a newline in the prompt box instead of submitting.
Activation spawns one `klorb server` process and creates one ACP session, shared by the whole
extension; **Klorb: Restart Server** kills and respawns the child and re-initializes the ACP
connection (picking up any change to the `klorb.serverPath`/`klorb.configPath` settings or the
stored OpenRouter API key — see "Configuration" below); **Klorb: New Session** replaces
the live ACP session with a fresh one (`session/new`) and clears the panel's history, without
restarting the child process. The panel's top title bar shows the active session's title (see
"Status row and session controls" below); a status row docked under the prompt input shows the
model chip, the thinking chip, a clickable permission-mode badge, and the token tally.
**Klorb: Select Model**, **Klorb: Set Thinking**, **Klorb: Cycle Permission Mode**, **Klorb:
Show Session Stats**, and **Klorb: Reload Skills** drive the same session-control surface from
the command palette.

## How it works

`vscode-plugin/src/` splits by JavaScript runtime at the top level — `src/host/` (extension
host, Node/CommonJS), `src/webview/` (webview UI, sandboxed browser document), `src/shared/`
(types/utilities included by both) — with `test/` mirroring that tree file-for-file. See
`AGENTS.md`'s "vscode-plugin source tree" section for the full directory-layout and import
conventions (the `features/<name>/` barrel pattern, the `shared/*`/`webview/*`/`host/*` rooted
import aliases, and the React default-export convention); this spec covers how those pieces fit
together for this extension specifically.

* `vscode-plugin/src/extension.ts` is the extension's activation entry point
  (`package.json`'s `main`, bundled to `out/extension.js`). `activate()` constructs one
  `KlorbServerProcess`, one `KlorbSessionViewProvider`, one `AcpConnection` wired to both
  (the provider is the connection's `SessionUpdateListener`; `provider.setConnection()`
  completes the reference cycle after construction, since the provider and connection
  otherwise have no way to point at each other from a single constructor call), one
  `SessionControls` wrapping the connection (`provider.setSessionControls()` completes that
  reference cycle the same way), one `ApiKeyManager`, and one `WorkspaceTrustBridge` (see
  "Status row and session controls" below for all four). It starts the connection via
  `readServerOptions()` (reads `klorb.serverPath`/`klorb.configPath` off
  `vscode.workspace.getConfiguration('klorb')` and resolves the OpenRouter API key through
  `ApiKeyManager.resolve()`, folding it into the child's `OPENROUTER_API_KEY` environment
  variable when defined) and `sessionCwd()` (the first workspace folder, or the home directory
  if none is open — ACP requires an absolute `cwd` for `session/new`), registers the view
  provider for the `klorb.sessionView` webview view, and registers `klorb.newSession` (calls
  `AcpConnection.newSession()` for a fresh conversation, then posts a `sessionReset` message to
  clear the panel) and `klorb.restartServer` (calls `AcpConnection.start()` again — `start()`
  stops any prior child first, so this both applies changed settings and recovers from a
  crashed/hung server). `context.subscriptions` stops the connection (killing the child
  process) when the extension deactivates. A handshake or connection failure at any of these
  points surfaces as both a VS Code error notification and a `turnError` panel message, rather
  than failing silently. `activate()` also creates the "Klorb" output channel
  (`vscode.window.createOutputChannel`, selectable from VS Code's Output panel dropdown) and
  passes an `appendLine`-backed `LogFn` into `AcpConnection`, so both the extension's own
  diagnostic logging and the `klorb server` child's stderr (see `AcpConnection` below) land in
  one place instead of the void `console.log` would otherwise go to.
* `vscode-plugin/src/host/klorbServerProcess.ts`'s `KlorbServerProcess` owns spawning, killing,
  and restarting the one `klorb server` child process — nothing about the wire protocol spoken
  over its stdio. `start()` stops any running child, spawns `<command> server` (appending
  `--config <configPath>` when non-empty) via an injected `SpawnFn` (defaulting to real
  `child_process.spawn`, so tests can drive the class against a fake process), and returns the
  new `ChildProcessWithoutNullStreams` so the caller can bind a protocol connection to its
  stdio. `stop()` kills the child if one is running.
* `vscode-plugin/src/host/features/acp/acpConnection.ts`'s `AcpConnection` owns the ACP
  client-side connection to the child: `start(options, cwd)` spawns the child via
  `KlorbServerProcess`, builds an
  `acp.ndJsonStream()` over its stdout/stdin (via Node's `Readable.toWeb()`/`Writable.toWeb()`,
  bridging the child's Node streams to the Web Streams API the SDK's stream type expects), and
  constructs the SDK's `ClientSideConnection` with a `KlorbAcpClient` as its `Client`
  implementation. It also pipes the child's `stderr` -- where klorb's Python `logging` output
  goes -- to `_log` line-by-line (`_pipeStderr()`, buffering a trailing partial line and
  chunk-split multi-byte characters via `StringDecoder` until a newline or stream close
  completes them), so server-side log output reaches the same sink (the "Klorb" output channel
  in the real extension, see `extension.ts` above) as the connection's own diagnostics rather
  than an unread pipe. It performs `initialize()` (asserting the negotiated `protocolVersion`
  matches `acp.PROTOCOL_VERSION`; a mismatch or a hung/failed handshake — bounded by a 10-second
  timeout — throws a readable error naming `klorb.serverPath` as the likely fix, covering an old
  pre-ACP klorb binary), advertising `clientCapabilities._meta.klorb.raiseToolCallLimit = true`
  (see [[klorb-server]]'s extension-methods section), records whether the server's own reply
  advertised `agentCapabilities._meta.klorb.enqueueMessage` into a private
  `_enqueueMessageCapable` flag (`klorbAgentCapability()`, a small pure reader over the raw
  `InitializeResponse`), and then `newSession(cwd)`, storing the returned `sessionId` and
  forwarding the response's `modes`/`_meta.klorb.workspace`/`.title` plus that capability flag
  to the listener as a `SessionInfo` via `onSessionInfo()` (`sessionInfoFromResponse(session,
  this._enqueueMessageCapable)`, see "Status row and session controls" below and "Queued
  messages and interrupt polish" above — the flag is a connection-lifetime property, re-supplied
  to every `newSession()` call rather than re-derived per session, and reset to `false` in
  `stop()`/`_handleClosed()`). `prompt(text)` sends `session/prompt` with one `TextContentBlock`
  and resolves with the ACP `stopReason`; only one prompt may be in flight at a time (matching
  [[klorb-server]]'s own one-prompt-at-a-time rule), so a second call while one is running
  rejects immediately rather than queuing -- a client with a message to add to the running turn
  calls `enqueueMessage(text)` instead (`extMethod('_klorb/enqueueMessage', {text})`; gated by
  the `enqueueMessageCapable` getter before the caller ever tries, see "Queued messages and
  interrupt polish" above). `cancel()` sends `session/cancel` as a
  fire-and-forget notification — the in-flight `prompt()` call still resolves normally once the
  server winds the turn down and replies with `stopReason: "cancelled"`. `setSessionMode(modeId)`
  sends `session/set_mode`; `extMethod(method, params)` calls a `_klorb/*` extension method
  against the live session, injecting `sessionId` into `params` automatically -- both are the
  low-level wire calls `SessionControls` builds its typed control-plane surface on top of.
  `stop()` kills the child and rejects any in-flight `prompt()` with a restart-style error; the
  same rejection fires automatically if the connection closes out from under an in-flight
  prompt (child crash, unexpected EOF). `newSession(cwd)`/`loadSession(cwd, sessionId)` each
  interrupt an in-flight turn first (`_interruptInFlightTurn()`): they send `session/cancel` for
  the session being replaced (so the provider's own streaming request is actually torn down, not
  just the client's wait for it) and immediately reject that turn's `prompt()` promise rather
  than waiting for the server's eventual response, so clicking "New session" (or loading a saved
  one) while a turn is running doesn't leave the old turn's tokens streaming into the new
  session's view. `_interruptInFlightTurn()` also bumps a `turnGeneration` counter (exposed via
  the `turnGeneration` getter, also bumped by `stop()`/`_handleClosed()`) that
  `KlorbSessionViewProvider._runTurn()` compares before and after its own `prompt()` call, so it
  can tell a superseded turn's settled promise apart from a live one and skip posting
  `turnEnded`/`turnError`/`serverLost` for it. `errorMessage()` (exported alongside the class) renders
  both real `Error` instances and the SDK's plain `{code, message}` JSON-RPC rejection objects
  as a readable string, since ACP request failures reject with the latter shape, not an
  `Error`. The `client` getter exposes the live `KlorbAcpClient` (`undefined` before `start()`
  completes or after `stop()`), which `KlorbSessionViewProvider` uses to forward a
  `permissionDecision` webview message and to trigger `repostPendingAsk()` after the webview
  view is recreated (see "Approval panel" below).
* `vscode-plugin/src/host/features/acp/klorbAcpClient.ts`'s `KlorbAcpClient` implements the ACP
  SDK's `Client` interface: the handler for requests/notifications the server sends back over
  the connection. Its constructor takes an optional `currentSessionId` getter (`AcpConnection`
  wires `() => this._sessionId`); `sessionUpdate()`/`extNotification()` each drop anything
  tagged with a `sessionId` other than that getter's current value (logging it and returning),
  rather than forwarding it to the listener -- this is what stops a turn `AcpConnection.
  newSession()`/`loadSession()` already interrupted from streaming stale `session/update`s into
  the new session's view during the window before the server's own turn actually winds down. No
  filtering happens when the getter is omitted (every existing test constructing this class
  directly, without an `AcpConnection`, relies on that default).
  `sessionUpdate()` dispatches `agent_message_chunk`/`agent_thought_chunk` text content to a
  `SessionUpdateListener` (`onAgentText`/`onThoughtText`), flattens `tool_call`/`tool_call_update`
  updates into `ToolCallStartedMessage`/`ToolCallUpdatedMessage` (`onToolCallStarted`/
  `onToolCallUpdated` — see "Tool-call rendering and editor integration" below), dispatches
  `current_mode_update`/`session_info_update` to `onModeChanged`/`onSessionTitleChanged` (the
  latter only when the update carries a `title` field at all), flattens `plan` into a
  `TaskListUpdateMessage` (`onTaskListUpdate` — see "Task panel" below), and logs (rather than
  errors on) any other update kind. `extNotification()`
  dispatches `_klorb/usage` to `onUsageUpdate(usedTokens, maxTokens)` (logging and ignoring a
  malformed payload), `_klorb/messageQueued`/`_klorb/queuedMessageSent` to `onMessageQueued(text)`/
  `onQueuedMessageSent(text)` (same malformed-payload handling; see "Queued messages and
  interrupt polish" above), and logs-and-ignores any other extension notification, per ACP's own
  extensibility rules. `requestPermission()` and the `_klorb/raiseToolCallLimit` extension
  method are covered in "Approval panel" below. `readTextFile()`/`writeTextFile()`/
  `createTerminal()` throw the SDK's
  `RequestError.methodNotFound()` synchronously, matching the client never advertising the
  `fs`/`terminal` capabilities during `initialize()`. `src/host/features/acp/index.ts` is this
  feature's barrel, re-exporting `AcpConnection`, `errorMessage`, `KlorbAcpClient`, `LogFn`,
  `PermissionDecisionResult`, `RaiseToolCallLimitFn`, and the `SessionInfo`/`SessionUpdateListener`
  types — the only things `extension.ts`, `klorbSessionViewProvider.ts`, and the
  `sessionControls` feature (outside the feature) import.
* `vscode-plugin/src/host/klorbSessionViewProvider.ts`'s `KlorbSessionViewProvider` implements
  `vscode.WebviewViewProvider` and `SessionUpdateListener`. `resolveWebviewView()` enables
  scripts, restricts `localResourceRoots` to the extension's own install directory, sets the
  webview's HTML, and registers `onDidReceiveMessage` to parse and dispatch `WebviewMessage`s
  (see "Webview message protocol" below) -- a rejection from that dispatch (`_handleMessage()`,
  `async`) is caught and logged via `_log` rather than left to become an anonymous
  unhandled-rejection warning, since `onDidReceiveMessage`'s callback itself can't be `async`
  and return a promise VS Code would await/reject on. `onAgentText`/`onThoughtText` (the
  `SessionUpdateListener` methods `AcpConnection` calls as chunks stream in) post `agentChunk`/
  `thoughtChunk` host messages; `onToolCallStarted`/`onToolCallUpdated` post the flattened
  `toolCallStarted`/`toolCallUpdated` messages as-is, and `onToolCallUpdated` additionally
  records any `diff` payload with the shared `EditorIntegration` (see "Tool-call rendering and
  editor integration" below) before posting, since the diff text isn't retained anywhere else
  once flattened into the webview message. `onSessionInfo`/`onModeChanged`/
  `onSessionTitleChanged`/`onUsageUpdate` (see "Status row and session controls" below) each
  delegate straight to the matching `SessionControls.apply*()` method, set via
  `setSessionControls()` the same way `setConnection()` wires the connection.
  `onMessageQueued`/`onQueuedMessageSent` (see "Queued messages and interrupt polish" above)
  post the matching `messageQueued`/`queuedMessageSent` host messages verbatim. `_runTurn(text)`
  — invoked for a `submitPrompt` message — posts `turnStarted`, awaits `AcpConnection.prompt
  (text)`, and posts either `turnEnded {stopReason}` or, on rejection, `serverLost {message}`
  when `connection.isReady` is now `false` (the child process itself was lost mid-turn) or
  `turnError {message}` otherwise (see "Queued messages and interrupt polish" above); `
  _enqueueMessage(text)` — invoked for an `enqueueMessage` message — checks `connection.isReady`
  and `connection.enqueueMessageCapable` before calling `AcpConnection.enqueueMessage(text)`,
  posting a `turnError` on either failure; a `cancelTurn` message calls `AcpConnection.cancel()`
  directly, with no reply of its own (the in-flight prompt's own `turnEnded`/`turnError`/
  `serverLost` follow-up is the confirmation); a `restartServer` message executes the
  `klorb.restartServer` command; `openLocation`/`openDiff` messages are routed to
  `EditorIntegration.openLocation()`/`openDiff()`; a `permissionDecision` message is routed to
  `AcpConnection.client?.resolvePermissionDecision()` (see "Approval panel" below);
  `pickModel`/`cyclePermissionMode` messages execute the `klorb.selectModel`/
  `klorb.cyclePermissionMode` commands, so a status row click drives the same code path as its
  command-palette equivalent.
  `postPermissionAsk()` (the `SessionUpdateListener` method `KlorbAcpClient` calls to post an
  ask) posts the `permissionAsk` host message and, if the view is hidden at that moment, shows a
  "Klorb needs your approval" notification with a "Show Klorb" action
  (`vscode.commands.executeCommand('klorb.sessionView.focus')`) so an approval can't sit
  invisible forever. `resolveWebviewView()` additionally calls
  `AcpConnection.client?.repostPendingAsk()` once the webview's message handler is wired up, so
  an ask still awaiting an answer from before a webview reload is shown again to the fresh
  webview instance, and `SessionControls.postSnapshot()` the same way, so the status row sees
  the current control-plane state instead of its placeholder defaults. `restart()` re-sets the
  webview's HTML
  (with a fresh nonce and a cache-busting query string on the compiled webview script's URI),
  which is what `klorb.newSession` calls — it reloads the panel's webview document (and
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
  overflows the panel). The page's `Content-Security-Policy` meta tag sets `default-src 'none'`
  (nothing loads unless a more specific directive allows it), `style-src`/`script-src` scoped to
  `webview.cspSource`/the per-load nonce, `connect-src ${webview.cspSource}` (so Chrome DevTools'
  own background fetch of `out/webview/main.js.map` -- when a developer opens **Developer: Open
  Webview Developer Tools** to inspect a crash -- isn't blocked by the `default-src 'none'`
  fallback, which would otherwise leave the console showing only minified bundle positions with
  no source-mapped file/line), `font-src ${webview.cspSource}` (so the codicon web font's own
  `@font-face` fetch isn't blocked either — see "Component library" below), and `img-src
  ${webview.cspSource} data:` (`data:` specifically for the base64 `<img>` thumbnails "Image
  attachments" above renders — without it, `default-src 'none'` silently blocks every image load,
  rendering the browser's own broken-image icon with no visible error). A `<link
  id="vscode-codicon-stylesheet">` for the build-generated `out/media/codicon.css` is emitted
  before `main.css`'s own `<link>` — `<vscode-icon>` looks for that exact element id to find the
  font stylesheet, and
  renders an empty glyph (silently, with only a console warning) if it isn't there.

### Webview UI structure

* `vscode-plugin/src/webview/main.tsx` is the webview's entry point, bundled separately (see
  "Build" below) and loaded as a plain classic `<script>`. It imports the
  `@vscode-elements/elements` custom-element modules used by the panel (registering
  `<vscode-textarea>`/`<vscode-button>` with the browser), calls `acquireVsCodeApi()` exactly
  once, reads any persisted `SessionState` via `webview/sessionState.ts`'s `readPersistedState()`,
  and mounts `<ErrorBoundary vscode={vscode}><App vscode={vscode} initialEntries={state.entries}
  .../></ErrorBoundary>` into `#root` with `react-dom/client`'s `createRoot()`. Calling
  `acquireVsCodeApi()` a second time anywhere throws and silently aborts whatever called it — the
  VS Code webview API only allows one call per page load — which is why the single `vscode` value
  from that one call is threaded through as a prop rather than re-acquired (see
  `docs/adrs/call-acquirevscodeapi-exactly-once-per-webview-page.md`).
* `vscode-plugin/src/webview/sessionState.ts`'s `readPersistedState()` reads `vscode.getState()`
  and sanitizes it before trusting it as `SessionState`: `entries` is filtered through
  `isHistoryEntry()` (`webview/features/history`'s `HistoryEntry` type guard — a non-null object
  with a recognized `kind`), dropping anything else. This logic lives in its own module rather
  than inline in `main.tsx` specifically so it's unit-testable — `main.tsx` self-executes `main()`
  at module scope, so importing it for a test would run it. Unlike the host↔webview message
  channel (`parseHostMessage`/`parseWebviewMessage`), persisted webview state was never
  runtime-validated before this, and it can outlive the extension version that wrote it (observed
  surviving a `.vsix` reinstall): a stale `entries` array holding a bare non-object value from an
  incompatible older build reached `historyModel.ts`'s `finishStreaming()` unchecked, whose own
  `'streaming' in entry` check throws a `TypeError` on a non-object — crashing the whole webview
  blank the moment any turn ended. `finishStreaming()` itself also grew a defensive `typeof`/`null`
  guard as a second layer, so a value that somehow still isn't an object (any future field this
  sanitization doesn't cover) degrades to being left alone rather than thrown on. Only `entries`
  gets this treatment; `pendingInteraction`/`status`/`taskList`/`taskPanelVisible` are trusted as
  before, since none of them are indexed into by shape the way a `HistoryEntry[]` reducer is.
* `vscode-plugin/src/webview/components/ErrorBoundary.tsx`'s `ErrorBoundary` is a class component
  (React error boundaries must be classes) wrapping the whole `<App>` tree: `getDerivedStateFromError()`
  swaps its render to a plain-text `.webview-crash` fallback ("Klorb panel crashed: `<message>`",
  with a pointer to **Developer: Open Webview Developer Tools** and the "Klorb" output channel)
  instead of leaving the whole webview blank — React unmounts everything below the nearest error
  boundary (or the whole root, with none) the instant a render throws. `componentDidCatch()`
  additionally posts a `webviewError` message (`{type: 'webviewError', message: string, stack?:
  string}`), which `KlorbSessionViewProvider._handleMessage()` logs via its own `LogFn` to the
  "Klorb" output channel — the webview runs in its own sandboxed `vscode-webview://` document
  with its own separate JS console, so an uncaught render exception there otherwise never reaches
  anywhere the extension's own logging goes. An error boundary only catches render-phase/lifecycle
  errors, not ones thrown from an event handler or async callback, so this is a backstop for a
  crashing render, not a catch-all.
* `vscode-plugin/src/webview/App.tsx`'s `App` component is the panel's layout shell, top to
  bottom: the title (`.title`, `sessionTitleText()` — the active session's `sessionTitle`, or
  `New session…` until one arrives, with an `(Untrusted)` suffix appended whenever
  `workspaceTrusted === false`, TUI header parity), `TaskPanel` (see "Task panel" below),
  `HistoryView`, an `#interaction-area` div
  that mounts `ApprovalPanel` while a permission ask is outstanding or `QuestionPanel` while an
  `AskUserQuestions` question is outstanding, `PromptInput`, and `StatusRow` (see "Status row
  and session controls" below). It owns all interactive state: `entries` (a `HistoryEntry[]`,
  seeded from `initialEntries`), `inFlight` (whether a turn is currently running),
  `pendingInteraction` (a `PermissionAskMessage
  | QuestionAskMessage | undefined`, seeded from `initialPendingInteraction` — see "Approval and
  question panels" below), `status` (a `StatusSnapshot`, seeded from `initialStatus` — see
  "Status row and session controls" below), `taskList` (a `TaskListSnapshot | undefined`, seeded
  from `initialTaskList` — see "Task panel" below), and `taskPanelVisible` (a `boolean`, seeded
  from `initialTaskPanelVisible ?? true` — see "Task panel" below). A `window` `message` listener
  parses each incoming payload with `parseHostMessage()`
  and applies it to `entries`/`inFlight`/`pendingInteraction`/`taskList` via the pure functions in
  the `features/history` feature, replaces `status` wholesale with a `statusUpdate` message's
  own fields (never merged — the host always posts the complete currently-known snapshot, see
  "Status row and session controls" below), and flips `taskPanelVisible` on a `toggleTaskPanel`
  message. Submitting a prompt while idle appends a `'prompt'` entry optimistically, sets
  `inFlight` to `true` (the host's own `turnStarted`/`turnError` follow-up confirms or corrects
  it), and posts `{type: 'submitPrompt', text}`; submitting while a turn is already in flight
  instead posts `{type: 'enqueueMessage', text}` with no optimistic entry of its own — the
  queued-message history entry comes from the host's own `messageQueued` echo instead, since
  it's the server, not the webview, that actually accepted the message (see "Queued messages"
  below). Toggling one chip's own chevron flips that
  entry's `expanded` flag (`applyToolCallExpandedToggle`) — the handler is passed down to
  `HistoryView`, which also receives `onRestartServer` (posts `{type: 'restartServer'}`, wired to
  a `'serverError'` entry's action button — see "Queued messages and interrupt polish" below).
  `handleApprovalDecision()` (passed to `ApprovalPanel` as `onDecision`) appends an
  `appendInteraction()` record, clears `pendingInteraction`, and posts `{type:
  'permissionDecision', ...}` back to the host; `handleQuestionAnswer()` (passed to
  `QuestionPanel` as `onAnswer`) is the parallel handler, appending an `appendQuestionInteraction()`
  record and posting `{type: 'questionAnswer', ...}`; `pickModel()`/`cyclePermissionMode()`
  (passed to `StatusRow` as `onPickModel`/`onCyclePermissionMode`) post `{type: 'pickModel'}`/
  `{type: 'cyclePermissionMode'}`; `toggleTaskPanelVisible()` (passed to `TaskPanel` as
  `onToggleVisibility`, and also invoked directly on an incoming `toggleTaskPanel` message) flips
  `taskPanelVisible`. A separate `useEffect` keyed on
  `entries`/`pendingInteraction`/`status`/`taskList`/`taskPanelVisible` calls `vscode.setState({
  entries, pendingInteraction, status, taskList, taskPanelVisible})` (so history, an unanswered
  interaction, the status snapshot, and the task panel's data/visibility all survive
  `retainContextWhenHidden`'s context teardown/rebuild). A third `useEffect`, keyed only on
  `entries`/`pendingInteraction`/`status` (deliberately *not* `taskList`/`taskPanelVisible`),
  scrolls the history's last child into view -- a `taskListUpdate` can arrive several times per
  turn (once per `TodoCreate`/`TodoUpdate`/`TodoNext` call), and scrolling on every one of those
  fights the browser's own attempt to keep a focused element elsewhere on the page (e.g. the task
  panel's own `<summary>`) in view, which visibly reads as the history freezing until focus moves
  away -- but only actually scrolls while `pinnedToBottomRef` (a ref, not state, updated by a
  `scroll` listener on the history container via `isScrollPinnedToBottom()`, mirroring the TUI's
  `_history_pinned_to_bottom`/`pinned_to_bottom()`) reads `true`, so a user who's scrolled up to
  reread earlier output isn't yanked back to the bottom by new content arriving. Two more
  `useEffect`s reclaim focus for `PromptInput` (via its own imperative `focus()`, exposed through
  a `ref`/`PromptInputHandle` — see its own bullet below): one keyed on `inFlight`, firing once a
  turn is no longer running; one keyed on `pendingInteraction`, firing once an approval/question
  panel resolves — mirroring the TUI's `_finish_turn()`, which always hands focus back to its
  input box once a turn is done. `App`'s returned tree is wrapped in `<VsCodeApiProvider
  vscode={vscode}>` (see below) so any descendant can reach `vscode` via `useVsCodeApi()` without
  it being threaded through as an explicit prop down every intermediate component.
* `vscode-plugin/src/webview/components/VsCodeApiProvider.tsx` and `vscode-plugin/src/webview/
  hooks/useVsCodeApi.ts` are the top-level (not feature-specific) pieces that distribute the
  `vscode` object: `VsCodeApiProvider` wraps a React context around the single `vscode` value
  `App` received as a prop, and `useVsCodeApi()` reads it back out (throwing if called outside
  the provider). `VsCodeApiProvider.tsx` is also the `VsCodeApi` interface's canonical home —
  `App.tsx`/`main.tsx`/tests import the type from there. Because `acquireVsCodeApi()` may only
  be called once per page load (see the ADR referenced above), this is the only sanctioned path
  to the `vscode` object for anything that isn't `main.tsx`/`App.tsx` themselves.
* `vscode-plugin/src/webview/features/history/` is the `history` feature: `historyModel.ts`
  holds the pure, React-independent reducer logic for the history list (kept separate
  specifically so it's unit-testable without mounting anything), `renderDiffLines.ts` is the
  pure diff-hunk-to-row-model helper `ToolCallChip` maps over, and `components/HistoryView.tsx`/
  `components/ToolCallChip.tsx` render them. `index.ts` is the feature's barrel — the only
  module anyone outside `features/history/` may import (as `webview/features/history`, per this
  repo's absolute-import convention — see `AGENTS.md`'s "vscode-plugin source tree" section).
  * `HistoryEntry` is `TextHistoryEntry | ToolCallHistoryEntry`. `TextHistoryEntry` is
    `{kind: 'prompt' | 'response' | 'thinking' | 'error' | 'notice' | 'interaction', text,
    streaming: boolean}` — an `'interaction'` entry is always `streaming: false`, a compact
    permanent record of an answered permission ask (see "Approval panel" below).
    `ToolCallHistoryEntry` is `{kind: 'toolCall', callId, status: 'in_progress' | 'completed' |
    'failed', title, toolKind, locations, contentText?, diff?, expanded: boolean}` — `toolKind`
    and `status` are plain strings (mirroring `HostMessage`'s own `kind`/`status` fields), and
    `expanded` is this one chip's own collapsed/expanded state (see below).
  * `appendPrompt(entries, text)` appends a finished `'prompt'` entry. `appendInteraction(entries,
    ask, decisionName)` appends an `'interaction'` entry recording an answered `PermissionAskMessage`:
    the header line (`"Privilege escalation"` when `ask.klorbMeta.escalation` is set, else
    `"Permission requested: <ask.klorbMeta.headerKind>"`), `ask.klorbMeta.resourceDescription`
    when present, `ask.klorbMeta.itemCommandText`/`commandText` when present, and `"Decision:
    <decisionName>"` — the TUI's `_record_interaction_history` equivalent.
    `appendQuestionInteraction(entries, ask, answerText)` is the same record for an answered
    `QuestionAskMessage`: `"Question <index+1> of <total> · <header>"`, the question text, and
    `"Answer: <answerText>"`.
  * `applyHostMessage(entries, message)` is the `HostMessage`
    reducer: `agentChunk`/`thoughtChunk` extend the trailing streaming entry of the matching
    kind, or start a new one if the last entry is a different kind (or not currently streaming)
    — so thinking and response phases interleave correctly across a turn. `turnEnded` finalizes
    every streaming entry's `streaming` flag and, for any `stopReason` other than `"end_turn"`,
    appends a `'notice'` entry naming the reason. `turnError` finalizes streaming entries and
    appends an `'error'` entry. `sessionReset` clears the list. `toolCallStarted` appends a new
    `'toolCall'` entry with `status: 'in_progress'` and `expanded` set to `false`.
    `toolCallUpdated` mutates the matching `callId`'s entry in place
    (status/title/content/diff/locations), or appends a new entry if no `toolCallStarted` for
    that `callId` was ever seen — the fallback for a call that failed before
    `on_tool_call_started` could fire (e.g. malformed arguments).
  * `applyTurnFlag(inFlight, message)` is the parallel reducer for the `inFlight` boolean:
    `turnStarted` raises it, `turnEnded`/`turnError`/`sessionReset` clear it, every other
    message leaves it unchanged.
  * `applyToolCallExpandedToggle(entries, callId)` flips a single `toolCall` entry's `expanded`
    flag (a chip's own chevron), leaving every other entry untouched.
  * `applyPendingInteraction(pendingInteraction, message)` is the parallel reducer for `App`'s
    `pendingInteraction` state: a `permissionAsk` or `questionAsk` message replaces it (the
    server never sends a concurrent second one, of either kind), `sessionReset` clears it, every
    other message leaves it unchanged. A resolved decision/answer clears `pendingInteraction`
    through `App`'s own `handleApprovalDecision()`/`handleQuestionAnswer()`, not through this
    reducer.
  * `HistoryView` renders the scrolling `HistoryEntry[]` list: `'prompt'` entries as a
    right-aligned `.bubble` (index-keyed — safe here specifically because entries only ever
    append, never reorder or get removed or inserted in the middle, the one case React's own
    docs call out as fine for index keys); `'response'` entries through `react-markdown`;
    `'thinking'` entries as a collapsed-by-default `<details>` disclosure (muted/italic styling,
    matching the TUI's thinking block) that keeps streaming into its body while the reader has
    it open; `'error'`/`'notice'`/`'interaction'` entries as plain styled text; `'toolCall'`
    entries as a `ToolCallChip`; `'sessionStats'` entries as a `SessionStatsCard` (see "Status
    row and session controls" below).
* `vscode-plugin/src/webview/components/PromptInput.tsx` renders the `<vscode-textarea>` +
  `<vscode-button>` input row: disabled while `inFlight` is true, unless `enqueueMessageCapable`
  (see "Queued messages and interrupt polish" below for that prop and the Send/Stop button
  logic it drives), and additionally styled with the `.input-row-muted` CSS class (dimmed
  opacity) while its `muted` prop is set — `App` passes `muted={pendingInteraction !==
  undefined}`, since a permission ask or question ask always arrives mid-turn (`inFlight` is
  already true), so `muted` layers the TUI's interaction-mode visual treatment on top of the
  already-disabled (or, when capable, still-enabled) input rather than changing whether it's
  disabled. Its own `onKeyDown` handles Shift+Tab (calls `onCyclePermissionMode`,
  `preventDefault`ed so it doesn't fall through to the browser's default tab-order navigation —
  mirroring the TUI's own Shift+Tab), Escape (calls `onCancel` when `inFlight`, regardless of
  `enqueueMessageCapable` — cancelling the turn is always available while one is running), and
  Enter, delegating the submit-vs-newline decision to `keyHandling.ts`'s `classifyEnterKey()`.
  While the `@`-mention file finder is open, it additionally claims ArrowUp/ArrowDown/Enter/Tab/
  Escape for finder navigation ahead of this handling (see "File finder (`@`-mention)" below).
* `vscode-plugin/src/webview/keyHandling.ts`'s `classifyEnterKey(shiftKey, ctrlKey)` returns
  `'newline'` if either modifier is held and `'submit'` otherwise. Pulling this one decision out
  as a standalone function is what makes it reachable from
  `vscode-plugin/test/webview/keyHandling.test.ts` without a browser, React, or a VS Code
  extension host.

### Tool-call rendering and editor integration

* `vscode-plugin/src/webview/features/history/components/ToolCallChip.tsx` renders one
  `ToolCallHistoryEntry` as a collapsed row plus (when `expanded`) its detail. The collapsed
  row shows: a `<vscode-progress-ring>` while `status === 'in_progress'`; an error-colored
  `error` `<vscode-icon>` when `status === 'failed'`; otherwise a codicon looked up from
  `toolKind` — `book` (read), `edit` (edit), `search` (search), `terminal` (execute), `globe`
  (fetch), `checklist` (think), `trash` (delete), `tools` (any other/unrecognized kind,
  including `other`) — via `KIND_ICON`, a `Record<string, string>` (not a switch, so an
  unrecognized `ToolKind` degrades to the generic icon instead of a type error). The title
  becomes a `<button>` posting `openLocation {path, line?}` (the entry's first `location`) when
  `locations` is non-empty, plain text otherwise; a trailing `<vscode-icon actionIcon>` chevron
  toggles this one chip's `expanded` flag. The expanded detail shows `contentText` as plain
  text, or — when `diff` is present — a colored gutter view (`renderDiffLines(diff.hunks)`'s row
  models, green `add`/red `del`/plain `context`, a `⋮` separator between hunks) when
  `diff.hunks` is present, else a plain `<pre>` of `diff.newText` (the fallback for a plain ACP
  diff block lacking `_meta.klorb.diffHunks` — not colored, since there's no hunk structure to
  render), plus an "Open diff" `<vscode-button>` posting `openDiff {callId, path}`.
* `vscode-plugin/src/webview/main.tsx` additionally imports `@vscode-elements/elements`'s
  `vscode-icon`/`vscode-progress-ring` modules (alongside `vscode-button`/`vscode-textarea`) to
  register those custom elements.
* `vscode-plugin/src/host/editorIntegration.ts`'s `EditorIntegration` bridges `openLocation`/
  `openDiff` webview messages to real VS Code editor integration. Its VS Code calls are all
  reached through an injected `EditorIntegrationVsCode` facade (the same "keep the real
  side-effecting API behind an injectable seam" shape `klorbServerProcess.ts`'s `SpawnFn` uses)
  — `editorIntegration.ts` itself only ever imports `vscode`'s *types* (`import type * as vscode
  from 'vscode'`), never its runtime value, so the module loads cleanly under `vitest` without a
  running VS Code extension host; `extension.ts`'s `realEditorIntegrationVsCode()` builds the
  one real implementation, since `extension.ts` already imports real `vscode` values for
  wiring up the rest of the extension.
  * `openLocation(path, line?)` opens `path` via `vscode.workspace.openTextDocument`/
    `vscode.window.showTextDocument`, moving the cursor/selection/viewport to `line` (1-indexed,
    matching klorb's own line-numbering convention — see `klorb.tools.util.diff_lines.DiffLine`)
    when given. A path that can't be opened (deleted, renamed, outside the workspace) surfaces
    a `vscode.window.showWarningMessage` instead of throwing.
  * `recordDiff(callId, {oldText, newText})` — called by `KlorbSessionViewProvider.
    onToolCallUpdated()` whenever a `tool_call_update` carries a `diff` — keeps a bounded
    (50-entry, oldest-evicted) `Map` from `callId` to its diff payload, since the reconstructed
    before/after text isn't retained anywhere else once flattened into the webview message.
  * `openDiff(callId, path)` looks up `callId`'s recorded payload (warning instead of opening
    anything if none was recorded, e.g. after a window reload — `vscode.getState()` doesn't
    persist this host-side map) and shows a real `vscode.diff` between two read-only virtual
    documents served by a `KlorbDiffContentProvider` registered under the `klorb-diff:` scheme
    (one `EditorIntegration` per extension activation registers its own provider instance),
    titled `"<basename> (Klorb edit)"`. The reassembled before/after text is a hunk-context view,
    not necessarily the whole file (see docs/adrs/persist-diff-hunks-in-edit-result.md) — an
    elided view, the same caveat [[klorb-server]] records for the update's own `oldText`/
    `newText`.

### Approval and question panels

A permission ask (or `EscalatePrivileges` ask, distinguished by `klorbMeta.escalation`) renders
as an `ApprovalPanel` docked directly above the prompt input — the tall-narrow equivalent of
the TUI's interaction panel. An `AskUserQuestions` question (`_klorb/askUserQuestions`) renders
in the same spot as a `QuestionPanel`. Both share one "pending interaction" slot and queue on
both the host and webview sides, since the server only ever has one blocking ask outstanding at
a time regardless of kind (see docs/specs/klorb-server.md's threading-bridge section). The
`_klorb/raiseToolCallLimit` tool-call-limit ask is a separate, host-only native modal, not a
webview panel (see below).

* **Host-side plumbing.** `KlorbAcpClient` (`src/host/features/acp/klorbAcpClient.ts`) tracks the
  single outstanding interaction in one `_pendingInteraction` field — a `{kind: 'permission',
  message: PermissionAskMessage, resolve}` or `{kind: 'question', message: QuestionAskMessage,
  resolve}` — and serializes new interactions through one shared `_enqueueInteraction()` queue
  (`_interactionBusy`/`_interactionQueue`): while one is outstanding, a second concurrent
  interaction of either kind queues behind it rather than posting on top of the first; the first
  (idle-case) interaction posts synchronously, within its triggering call.
  `requestPermission()` flattens the ACP `RequestPermissionRequest` into a `PermissionAskMessage`
  (`title` from `toolCall.title`, `options` flattened to `{id, name, kind, scope?}` — `scope`
  from each option's own `_meta.klorb.scope` — and `klorbMeta` copied verbatim from the request's
  `_meta.klorb`), assigns it the next `requestId`, and posts it through
  `SessionUpdateListener.postPermissionAsk()`; `extMethod()`'s `_klorb/askUserQuestions` handling
  (`_askUserQuestions()`) validates the ext request's raw params into a `QuestionAskMessage` (a
  malformed/unrecognized shape from a future or buggy server resolves `{cancelled: true}`
  immediately, with no wire traffic and no interaction posted at all) and posts it through
  `SessionUpdateListener.postQuestionAsk()`. `resolvePermissionDecision(requestId, decision)`/
  `resolveQuestionAnswer(requestId, answer)` resolve the matching outstanding interaction (logging
  and ignoring a stale/unknown `requestId`, or one naming the wrong interaction kind) and build
  the result the waiting call is awaiting: a `cancelled` permission decision maps to `{outcome:
  {outcome: "cancelled"}}`; a selected one maps to `{outcome: {outcome: "selected", optionId,
  _meta: {klorb: {otherText}}}}` when `otherText` is set (the free-text redirect), else without
  the `_meta`; a question answer maps to `{selectedOptionIndex}`, `{otherText}`, or `{cancelled:
  true}` verbatim (see [[klorb-server]]'s extension-method registry for the exact result shape).
  `repostPendingInteraction()` re-posts the currently outstanding interaction's message unchanged,
  through whichever `SessionUpdateListener` method matches its kind — called by
  `KlorbSessionViewProvider.resolveWebviewView()` so an interaction from before a webview reload
  is shown again to the fresh webview instance, since the live `KlorbAcpClient` (and its
  still-pending ACP request) outlives the reload. `extMethod()` also answers
  `_klorb/raiseToolCallLimit` (params `{sessionId, message}`, see [[klorb-server]]) via the
  injected `RaiseToolCallLimitFn`, returning `{approved}`; any other ext method throws
  `RequestError.methodNotFound()`. `AcpConnection.start()` advertises
  `clientCapabilities._meta.klorb = {raiseToolCallLimit: true, askUserQuestions: true}` at
  `initialize()` and constructs `KlorbAcpClient` with `extension.ts`'s
  `raiseToolCallLimitModal()` — a `vscode.window.showWarningMessage(message, {modal: true},
  "Continue")` — as the injected function; `AcpConnection`'s `client` getter is what lets
  `KlorbSessionViewProvider` reach the live `KlorbAcpClient` for `resolvePermissionDecision()`/
  `resolveQuestionAnswer()`/`repostPendingInteraction()`.
  `KlorbSessionViewProvider.postPermissionAsk()`/`postQuestionAsk()` post the `permissionAsk`/
  `questionAsk` host message and, if the Klorb view is hidden at that moment
  (`WebviewView.visible`), also show a "Klorb needs your approval"/"Klorb has a question for
  you" notification (`_notifyIfHidden()`, shared by both) with a "Show Klorb" action
  (`vscode.commands.executeCommand('klorb.sessionView.focus')`) so an interaction can't sit
  invisible forever.
* **Webview `ApprovalPanel`** (`src/webview/components/ApprovalPanel.tsx`) is mounted in `App`'s
  `#interaction-area` while `pendingInteraction.type === 'permissionAsk'` (see the
  `App.tsx`/`historyModel.ts` bullets above). Top to bottom: a header reading `"Permission
  requested: <klorbMeta.headerKind>"` (or "Privilege escalation" — styled with the error-color
  accent via `.approval-panel-escalation` — when `klorbMeta.escalation` is set), with
  `itemIndex`/`itemTotal` rendered as `"<itemIndex + 1> of <itemTotal>"` when present;
  `klorbMeta.resourceDescription` and (for an escalation ask) `klorbMeta.escalation.description`;
  for a bash ask, `klorbMeta.itemCommandText` in a monospace block, with a "Show full command"
  `<details>` disclosure revealing `klorbMeta.commandText` (scrollable, height-capped via
  `.approval-command-full`) when it differs from `itemCommandText`; `klorbMeta.grantPatterns`
  (each pattern's argv tokens joined with spaces) as a dim "grants: ..." line;
  `klorbMeta.riskLevel`, when present, as a bordered `"Risk: <N>/10"` chip (`.approval-risk`,
  styled with an outline rather than a filled background so it reads as informational rather
  than another button) colored by a low/medium/high band (`riskBand()`, thresholds at 4 and 7),
  followed by `klorbMeta.riskRationale` (the classifier's one-sentence explanation), italicized
  and colored by the same band, when present; the option grid (below); and an "Other…" `<details>`
  disclosure with a `<vscode-textfield>` + "Send" button that submits the ask's own
  `reject_once`-kind option's id (falling back to the first option) with `otherText` set to the
  typed redirect. Escape (bubbling from anywhere in the panel) posts a `{cancelled: true}`
  decision. All `klorbMeta` field access is defensive (`typeof` guards, no throwing on a
  missing/malformed field), since `klorbMeta` is untyped pass-through data from the wire.
* **Option grid.** `groupOptionsByScope()` groups `ask.options` into one row per scope (`once`,
  `session`, `workspace`, `homedir`, in that order) with an Allow and a Deny cell each
  (`OptionCell`, one `<vscode-button>` per option present for that cell — `allow_*` kinds
  primary, everything else secondary — or an empty cell when this ask's options don't cover that
  scope/action pair, e.g. a `StructuralResource` ask's rows have no workspace/homedir column),
  rendered as a two-column CSS grid with "Allow"/"Deny" column headers — the same allow/deny-by-
  scope hierarchy as the TUI's own grid. Falls back to the previous flat wrapping row of buttons
  (in `ask.options` order, no scope grouping) whenever any option is missing its own `scope` or
  names a scope token this panel doesn't recognize -- e.g. a non-klorb ACP agent's options.
* **Webview `QuestionPanel`** (`src/webview/components/QuestionPanel.tsx`) is mounted in `App`'s
  `#interaction-area` while `pendingInteraction.type === 'questionAsk'`. Top to bottom: a header
  row with `ask.header` and a `"Question <index + 1> of <total>"` caption; `ask.question`; an
  option list (one `<vscode-button>` per `ask.options` entry, the label bold and, when present,
  the description dimmed below it) that posts `{selectedOptionIndex: index}` on click, omitted
  entirely for a plain free-text question (`ask.options` empty); and an "Other…" `<details>`
  disclosure (open by default when there are no listed options, since there's nothing else to
  interact with) with a `<vscode-textfield>` + "Send" button posting `{otherText}`. A closing
  hint line reads "Esc dismisses remaining questions" — Escape (bubbling from anywhere in the
  panel) posts `{cancelled: true}`, which stops the *whole* question batch server-side, not just
  the current question (see [[klorb-server]]'s `AskUserQuestions` section), unlike a permission
  ask's per-item Escape.
* **Message protocol** (`src/shared/webviewMessages.ts`): `PermissionAskMessage` (host → webview)
  is `{type: 'permissionAsk', requestId: number, title: string, options: PermissionAskOption[],
  klorbMeta: Record<string, unknown>}`, where `PermissionAskOption` is `{id, name, kind,
  scope?}`. `PermissionDecisionMessage` (webview → host) is a discriminated union — `{type:
  'permissionDecision', requestId, cancelled: true}` or `{type: 'permissionDecision', requestId,
  optionId: string, otherText?: string}` — so a consumer narrows on `'cancelled' in message`
  rather than checking an optional field's presence. `QuestionAskMessage` (host → webview) is
  `{type: 'questionAsk', requestId: number, header: string, question: string, options:
  QuestionAskOption[], index: number, total: number}`, where `QuestionAskOption` is `{label,
  description?}`. `QuestionAnswerMessage` (webview → host) is the parallel discriminated union —
  `{type: 'questionAnswer', requestId, cancelled: true}`, `{type: 'questionAnswer', requestId,
  selectedOptionIndex: number}`, or `{type: 'questionAnswer', requestId, otherText: string}`. All
  four are validated by dedicated guards in `parseHostMessage()`/`parseWebviewMessage()`, the same
  pattern `toolCallStarted`/`toolCallUpdated` already use for nested-object payloads.
* **Pending-interaction persistence.** `App`'s `pendingInteraction` state (not local
  `ApprovalPanel`/`QuestionPanel` state) is what `vscode.setState({entries, pendingInteraction})`
  persists, so an unanswered interaction survives a webview hide/show —
  `retainContextWhenHidden` already keeps the React tree alive across an ordinary hide/show, and
  this state persistence is the backstop for an actual webview reload, paired with the host-side
  `repostPendingInteraction()` re-post described above.
* **History record.** `historyModel.ts`'s `appendInteraction(entries, ask, decisionName)` (called
  from `App`'s `handleApprovalDecision()`) appends an `'interaction'`-kind `HistoryEntry` — the
  TUI's `_record_interaction_history` equivalent — rendered as plain styled text
  (`.entry-interaction`) in the history scroll once the panel unmounts.
  `appendQuestionInteraction(entries, ask, answerText)` (called from `App`'s
  `handleQuestionAnswer()`) is the same record for an answered question.

### File finder (`@`-mention)

Typing `@` into the prompt input (at the start of the text, or after whitespace) opens a popup
listing up to 25 fuzzy-matching workspace files and directories, scrollable within a fixed
~6-row-tall panel, mirroring the `ApprovalPanel`/`QuestionPanel` family's "docked panel above the
prompt input" look but driven entirely by local textarea state, not the ACP interaction protocol
those two share.

* **Workspace enumeration** (`vscode-plugin/src/host/features/fileSearch/fileSearch.ts`).
  `FileSearch.listWorkspaceFiles()` lists every file under the first workspace folder via
  `vscode.workspace.findFiles('**/*', ...)` (respecting VS Code's own `files.exclude` setting),
  then filters the result against every `.gitignore` in the workspace — `findFiles()` does *not*
  honor `.gitignore` on its own. Each nested `.gitignore`'s patterns are rewritten
  (`scopeGitignoreLine()`) to be relative to the workspace root (prefixed with the `.gitignore`'s
  own directory, and widened with a `**` unless the pattern is itself anchored) and flattened
  into one `ignore()` matcher (the `ignore` npm package) so a single pass enforces every
  `.gitignore` in the tree at once, the same net effect as `git ls-files` without needing a `git`
  binary on `PATH`. Capped at 20000 files (`MAX_WORKSPACE_FILES`). `FileSearchVsCode` is the
  usual injected-`vscode`-slice interface (mirrors `EditorIntegrationVsCode`) so the module only
  imports `vscode`'s types, never its runtime value; `extension.ts`'s `realFileSearchVsCode()` is
  the real implementation, constructing the `vscode.RelativePattern` `findFiles()` needs.
* **Keeping the list fresh without re-scanning.** A one-shot scan is a snapshot, not a live view:
  it wouldn't otherwise notice a file created/deleted (or a `.gitignore` edited) after it runs.
  `FileSearch.watch(onChanged)` covers that gap with two `vscode.FileSystemWatcher`s — one on
  `**/*` with `ignoreChangeEvents: true` (a file's *content* changing can't change whether it's in
  the list) and one on `**/.gitignore` with content changes *not* ignored (a `.gitignore` edit
  changes the filter itself) — and calls `onChanged(files: string[])` with the updated list,
  debounced by `WATCH_DEBOUNCE_MS` (400ms) so a burst of events (e.g. an `npm install` or branch
  checkout) resolves to one call, not one per event. Critically, an ordinary create/delete is
  applied as a single add/remove against the cached list and the `Ignore` matcher the last scan
  produced — classifying one path, not touching the filesystem again — since the watcher event
  already names the exact file that changed; only a `.gitignore` create/edit/delete falls back to
  a full re-scan (`_scan()`, shared with `listWorkspaceFiles()`), because the `ignore` package
  can't selectively un-apply previously added patterns, so the matcher itself has to be rebuilt
  from scratch. Concurrent flushes are serialized through a promise chain (`flushChain`) so a
  slow `.gitignore`-triggered rescan can't race a later debounced flush over the shared cache.
  `watch()` runs its own initial scan immediately on call (nothing to debounce yet) and calls
  `onChanged` once that lands, before any watcher event does; `FileSearchVsCode.
  createFileSystemWatcher(root, glob, ignoreChangeEvents)` is the corresponding addition to the
  injected-`vscode`-slice interface. `extension.ts`'s `activate()` creates one such watcher for
  the extension's lifetime (`context.subscriptions`), wired to `provider.setWorkspaceFiles(files)`
  — which both posts `{type: 'workspaceFiles', files: string[]}` (POSIX-style paths relative to
  the workspace root) and caches it in `KlorbSessionViewProvider._workspaceFiles`, so
  `resolveWebviewView()` can re-post that cached list to a freshly (re)resolved view — the same
  "repost cached state, don't recompute it" pattern `postSnapshot()` uses — instead of scanning
  again on every resolve. `App`'s `workspaceFiles` state holds the latest snapshot (not persisted
  via `vscode.setState()`: a webview reload gets a fresh post from the next `resolveWebviewView()`
  instead of risking a stale cached list) and is passed straight through to `PromptInput`.
* **Mention detection and matching** (`vscode-plugin/src/webview/features/fileFinder/`).
  `fileFinderModel.ts`'s `detectMentionQuery(text, cursor)` scans backward from the cursor for
  the nearest `@` not separated from it by whitespace, itself preceded by start-of-text or
  whitespace (so an email-like `foo@bar` doesn't trigger); returns the `@`'s index and the query
  typed after it. `useFileFinder(files)` (`useFileFinder.ts`) owns the finder's React state:
  `sync(text, cursor)` re-runs detection on every keystroke/cursor-move and, when a mention is
  active, ranks the query against one blended candidate list — `files` plus every ancestor
  directory of one (`ancestorDirectories()`, since `files` itself only ever lists files).
  `rankMatches` first splits the query at its last `/` (`splitQueryDirectory()`) into a literal
  directory prefix (e.g. `'klorb/'`) and a remaining fuzzy-match fragment (e.g. `'sr'`), and
  narrows candidates to real descendants of that prefix by a plain string-prefix check, not a
  fuzzy one, before any Fuse.js scoring happens: this is what makes selecting a directory match
  (which always inserts a trailing `/`, `buildDirectoryInsertion`) actually scope into that
  subtree instead of fuzzy-matching the directory's name against every workspace path, which
  could otherwise resurface an unrelated path that merely contains the same text (e.g. `.klorb/`
  would falsely match a `klorb/` prefix). An unprefixed query reuses the persistent `fuse` index
  memoized over the whole candidate list (`new Fuse(candidates, {keys: ['path'], threshold: 0.4,
  ignoreLocation: true, includeScore: true})`); a prefixed one builds a throwaway `Fuse` index
  over just the (already much smaller) scoped subset, keyed on each candidate's path *relative
  to the prefix* rather than its full path, since that relative string differs per prefix and
  can't be precomputed once. A directory candidate's Fuse score (0 = perfect match, 1 = no
  match) gets `DIRECTORY_SCORE_BUMP` (0.1) subtracted before ranking, so it surfaces above an
  equally-relevant file, then `DIRECTORY_DEPTH_PENALTY` (0.02) added back per `/` in its full
  path (`pathDepth()`), so among several directories that match the query about equally well,
  the one nearest the workspace root outranks one nested many levels deep. Fuse's own
  `threshold` already excludes non-matches before either adjustment is applied. Up to
  `MAX_MATCHES` (25) ranked results are kept (`rankFuseResults`) for a non-empty fragment — an
  empty fragment (including a bare empty query right after `@`) instead lists the scoped
  candidates via `breadthFirstMatches` with a different, two-tier priority: everything sitting
  directly in the current directory (its own subdirectories, then its own files) is always shown
  in full, with no size cutoff, however many there are; only after all of that does the list
  fill in with everything nested deeper (again subdirectories before files, then shallower
  before deeper, then alphabetically within a depth), and only up to `MAX_MATCHES` total. This
  keeps a directory's own files from being crowded out of the visible list by an unrelated, much
  larger subtree sitting alongside them — which a flat depth-then-alphabetical sort with a
  single size cutoff would otherwise do, since a parent directory's path is always a string
  prefix of its children's and nothing would stop a deep branch's many entries from filling the
  cutoff before a sibling file is ever reached. The finder resets to closed when there's no
  mention or (per the "keep typing rules everything out" behavior) zero matches.
  Escape (`dismiss()`) closes the popup without forgetting the mention itself — an
  `escapedStartRef` remembers which mention's `@` position was dismissed, so further typing
  within that same mention doesn't reopen it, but moving to a *different* `@` does.
* **`FileFinderPanel`** (`components/FileFinderPanel.tsx`) renders each match (`FinderMatch`:
  `{path, isDir}`) via `splitFinderPath()`, which splits a path at its last `/` into a `dirPart`
  and a fixed, never-truncated `filePart` carrying its own leading `/` (with a trailing `/`
  appended when `isDir` is true) — so a deep path reads as ".../path/to/file.txt" instead of
  wrapping or scrolling horizontally. `dirPart` truncates via CSS (`text-overflow: ellipsis` +
  `white-space: nowrap`), but from the *front*: `.file-finder-row-dir` sets `direction: rtl;
  text-align: left` (plus `unicode-bidi: plaintext` to keep the Latin path text itself in normal
  reading order), which moves the browser's end-of-string ellipsis to the visual front, since
  browsers don't natively support start-of-string ellipsis. This keeps the segment immediately
  before the filename visible — the most differentiating context when several rows share a long,
  common leading prefix — instead of the segment closest to the workspace root. The popup itself
  (`.file-finder-panel`) is `position: absolute; bottom: 100%` inside `PromptInput`'s own
  `.input-row` (which is `position: relative` for this purpose) rather than living in `App`'s
  `#interaction-area`: an overlay that doesn't push the input row down as matches change per
  keystroke, unlike the in-flow `ApprovalPanel`/`QuestionPanel`. `max-height` caps it at ~6 rows
  with `overflow-y: auto`, so it shrinks to fit when there are fewer matches than that, and
  scrolls to reach the rest of `MAX_MATCHES` otherwise; this cap is independent of `MAX_MATCHES`
  and must be updated by hand if the row height or desired visible count changes.
* **Keyboard/mouse wiring** (`PromptInput.tsx`). While the finder is open, `handleKeyDown`
  intercepts ArrowUp/ArrowDown (`finder.moveActive()`, wrapping at both ends), Enter/Tab
  (`applyFinderSelection()`), and Escape (`finder.dismiss()`) before its normal Shift+Tab/Escape/
  Enter handling runs; a click on a row (`FileFinderPanel`'s `onSelect`) does the same. Caret
  position is read via a `cursorPosition()` helper (`wrappedElement.selectionStart`, falling back
  to the element's own `selectionStart` and finally to end-of-text) fed from the textarea's
  `onInput` (every keystroke), `onKeyUp` (only ArrowLeft/ArrowRight/Home/End, which move the
  caret without an `input` event), and `onClick`.
* **Insertion** (`fileFinderModel.ts`'s `escapeMentionPath()`/`buildMentionInsertion()`/
  `buildDirectoryInsertion()`). `applyFinderSelection()` calls `useFileFinder`'s `select()`,
  which branches on the chosen match: a file match replaces the `@query` span with `@` followed
  by the chosen path and a trailing space, closing the finder, so the user can keep typing
  immediately as plain text — the `@` itself stays in the text. A directory match instead
  replaces the span with `@` followed by the escaped directory path and a trailing `/` (no
  space), then re-syncs the finder against that narrowed text instead of closing it, so the query
  keeps drilling into that subtree — a directory is never a resolvable `@mention` target on its
  own. `escapeMentionPath()` backslash-escapes, in this order (backslash first, so the escapes it
  introduces aren't themselves re-escaped by the later passes), a literal `\`, `"`, and space —
  e.g. a file named `foo bar.txt` inserts as `@foo\ bar.txt`. If the chosen path itself ends in a
  character from `TRAILING_MENTION_PUNCTUATION` (`fileFinder/mentionParser.ts` — see "Mention
  highlighting in history" below), `needsQuotedMention()` routes the insertion through the
  quoted form instead (`@"<path>"`, via `escapeQuotedMentionPath()`, which escapes only `\` and
  `"` — a quoted mention's contents need no space-escaping), since the unquoted form would
  otherwise have that trailing character trimmed back off once the prompt is parsed
  (`stripTrailingMentionPunctuation()`), no longer naming the file that was actually selected.

### Mention highlighting in history

Every `@mention` in an already-submitted `'prompt'`/`'queuedMessage'` history entry
(`HistoryView.tsx`) is wrapped in a `.mention-chip` span (fixed light-purple background,
dark-purple text — a self-contained pill, not derived from `--vscode-*` theme variables, so it
reads consistently regardless of the host's light/dark theme) by `MentionHighlightedText`
(`history/components/MentionHighlightedText.tsx`), so a submitted prompt visibly confirms
exactly which file references the server resolved.

* **Parsing** (`fileFinder/mentionParser.ts`). `findMentionSpans(text)` mirrors
  `klorb.session.mixins.mentions._AT_MENTION_RE`/`resolve_at_mentions` field-for-field in
  TypeScript — same two-branch regex (quoted `@"..."` vs. unquoted), the same
  `unescapeMentionFilename()`/`stripTrailingMentionPunctuation()` pair, and the same
  `TRAILING_MENTION_PUNCTUATION` set (now including `:`/`;` on both sides) — so the webview
  never has to ask the extension host to re-derive which spans are mentions; it just re-parses
  the same prompt text the server already parsed. Each returned `MentionSpan` is
  `{start, end, filename}`: `end` excludes trailing punctuation trimmed off an unquoted match,
  but includes a quoted mention's closing `"`, matching exactly the syntax
  `resolve_at_mentions()` would treat as the file reference. This module owns
  `TRAILING_MENTION_PUNCTUATION` — `fileFinderModel.ts`'s `needsQuotedMention()` imports it
  rather than keeping its own copy, so the finder's "does this path need quoting" check and the
  history view's "where does this mention actually end" check can never drift apart.
* **Rendering** (`MentionHighlightedText.tsx`). Splits `entry.text` at each `MentionSpan`,
  rendering the mention's own substring (`text.slice(span.start, span.end)`) inside a
  `<span className="mention-chip">` and everything between spans as plain text, preserving the
  original string byte-for-byte across the split (no re-escaping or reformatting) so the prompt
  still reads exactly as typed. Applied by `HistoryView.tsx` to both `'prompt'` and
  `'queuedMessage'` entries -- already-submitted text only. `PromptInput`'s live-typing
  `<textarea>` is plain text and unaffected; highlighting it would need a different rendering
  approach (an overlay or a rich-text editor) rather than this component, which only ever
  produces read-only JSX.

### Image attachments

`PromptInput` also owns a pending image-attachment tray, populated by drag-drop (`onDragOver`/
`onDrop` on the input row's wrapper), clipboard paste (`onPaste` on the textarea), or the status
row's "Attach Image…" menu item (a native file picker round trip via a new `attachImageFile`/
`imageAttached` message pair); each source converges on the same `ImageAttachment` shape
(`{mimeType, dataBase64, name?}`) added to local `attachments` state and rendered as a removable
thumbnail above the textarea. All three sources are gated on `imagesCapable`
(`StatusSnapshot.activeModelVision`, fetched alongside `model`/`thinking` via
`_klorb/getSessionConfig`): `false` or not-yet-known both hide the affordance entirely, since
attaching against a non-vision model can only fail server-side. `submit()` passes the pending
attachments through as `onSubmit`'s second argument; `AcpConnection.prompt()` turns them into
ACP `image` content blocks (a picked/dragged file's name riding `_meta.klorb.filename`, absent
for a clipboard paste). A submitted prompt's images also ride its history entry
(`TextHistoryEntry.images`), rendered as thumbnails in the scrolled history. See
docs/specs/vision-image-input.md for the full data flow (client-side MIME/size guards, the
server-side resize pipeline, and why `_klorb/enqueueMessage` rejects images outright rather than
silently dropping them).

### Queued messages and interrupt polish

A prompt submitted while a turn is already running is queued into it, not rejected, when the
server advertises [[klorb-server]]'s `_klorb/enqueueMessage`; a cancelled or errored turn (or a
lost server process) gets a distinct, TUI-parity history treatment rather than just quietly
stopping.

* **Capability plumbing.** `AcpConnection` records whether the server's `initialize()` reply
  advertised `agentCapabilities._meta.klorb.enqueueMessage` (`klorbAgentCapability()`, a small
  pure reader over the raw response) into a private `_enqueueMessageCapable` flag — a property
  of the *connection*, not any one session, so it's threaded into every `newSession()`'s
  `SessionInfo` (`sessionInfoFromResponse(session, this._enqueueMessageCapable)`) rather than
  re-derived per session. `SessionControls.applySessionInfo()` folds it into the running
  `StatusSnapshot` as `enqueueMessageCapable`, so it reaches the webview the same way every other
  piece of session-starting state does (a `statusUpdate` message), and `App` passes
  `status.enqueueMessageCapable` straight through to `PromptInput`.
* **`PromptInput`'s `enqueueMessageCapable` prop** (`src/webview/components/PromptInput.tsx`)
  changes what "disabled while `inFlight`" means: `disabled = inFlight && !enqueueMessageCapable`.
  When capable, the textarea stays enabled during a turn and both a "Send" and a "Stop" button
  render (Send posts through the same `onSubmit` `App` already wires to the enqueue-vs-submit
  branch below); without it, only "Stop" renders and the textarea disables, exactly as before
  this increment. The component now forwards a `ref` typed `PromptInputHandle` (`{focus():
  void}`, via `forwardRef`/`useImperativeHandle`) so `App` can reclaim focus imperatively (see
  its own bullet above) — calling `.focus()` on the `<vscode-textarea>` custom element relies on
  its shadow root's `delegatesFocus: true` to land the browser's actual focus on the inner
  `<textarea>`, not just the host element.
* **Webview → host `enqueueMessage`** (`{type: 'enqueueMessage', text: string}`,
  `shared/webviewMessages.ts`): `App`'s `submit()` posts this instead of `submitPrompt` whenever
  `inFlight` is already `true`, with no optimistic history append of its own (see the `App.tsx`
  bullet above for why). `KlorbSessionViewProvider._enqueueMessage()` checks `connection.isReady`
  and `connection.enqueueMessageCapable` (both should already hold, since the webview only posts
  this when it believes the capability is present — reaching here otherwise means the connection
  state changed underneath it) before calling `AcpConnection.enqueueMessage(text)` (`extMethod(
  '_klorb/enqueueMessage', {text})`), surfacing a `turnError` instead of silently dropping the
  message on either failure.
* **Host → webview `messageQueued`/`queuedMessageSent`** (`{type: 'messageQueued', text: string}`
  / `{type: 'queuedMessageSent', text: string}`): `KlorbAcpClient.extNotification()` dispatches
  the matching `_klorb/*` ext notifications (see [[klorb-server]]) to `SessionUpdateListener.
  onMessageQueued()`/`onQueuedMessageSent()`, which `KlorbSessionViewProvider` posts verbatim.
  `historyModel.ts`'s `appendQueuedMessage(entries, text)` appends a `'queuedMessage'`-kind
  entry (rendered as a `.bubble-queued` — the ordinary prompt bubble, italicized, with a small
  "Queued message" header line above it); `applyQueuedMessageSent(entries, text)` flips the
  *oldest* still-`'queuedMessage'` entry whose text matches to a regular `'prompt'` entry,
  confirming delivery — correlation is by order + text, matching the server's own single-queue
  reality (there's no stable id to match against instead). A `queuedMessageSent` with no
  matching entry (a stale/duplicate notification) is a no-op.
* **Interrupted-turn marker** (`historyModel.ts`'s `applyInterruptedMarker(entries)`, a pure
  function ported from the TUI's `_handle_aborted_response` decision table): `applyHostMessage()`
  calls it for a `turnEnded` whose `stopReason` is `"cancelled"` specifically (any other non-
  `"end_turn"` reason still gets the older generic `"Turn ended: <reason>"` notice). It appends
  `"\n\n*(interrupted)*"`/`"\n\n(interrupted)"` to whichever of the trailing `'response'`/
  `'thinking'` entry was still `streaming` when the turn ended (finalizing it in the same step),
  or — if neither was (e.g. cancelled between tool-call rounds, before the next round's text had
  started streaming) — appends a standalone `'notice'` entry reading `"(interrupted)"`.
* **Lost-server-process entries.** `KlorbSessionViewProvider._runTurn()`'s `catch` block checks
  `connection.isReady` after a rejected `prompt()` call: still ready means an ordinary turn
  failure (`turnError`, unchanged); no longer ready means the `klorb server` child itself was
  lost (crashed, or killed/restarted) out from under the turn, posted as a distinct `{type:
  'serverLost', message}` instead. `historyModel.ts` renders this as a `'serverError'`-kind entry
  (`applyHostMessage()`'s `serverLost` case, also finalizing streaming and clearing `inFlight`
  the same way `turnError` does); `HistoryView`'s `renderEntry()` gives it the same
  `.entry-error` styling as a plain error plus a "Restart Server" `<vscode-button>` (`
  onRestartServer`, threaded down from `App`'s own `restartServer()`, which posts `{type:
  'restartServer'}`). `KlorbSessionViewProvider` maps that webview message straight to the
  `klorb.restartServer` command, the same one the command palette entry runs.
* **Sweep pass.** Beyond the focus-reclaiming `useEffect`s and the pinned-to-bottom autoscroll
  gating (both described in the `App.tsx` bullet above), `sessionReset` clearing every model
  slot (`entries`, `pendingInteraction`, `taskList`, `inFlight`) was already correct going into
  this increment via each slot's own reducer; `status` is likewise always correct by the time a
  `sessionReset` message arrives, since `SessionControls.applySessionInfo()` — invoked
  synchronously inside the `connection.newSession()`/`connection.start()` call that precedes
  every `sessionReset` post — already resets it to the fresh session's real starting values (not
  stale ones) over the ordinary `statusUpdate` channel.

### Status row and session controls

Surfaces [[klorb-server]]'s control-plane surface (session modes, model/thinking config,
session stats, workspace trust, skills reload) as a docked status row plus native command-
palette commands — see docs/plans/archive/plan-016-009-vscode-session-controls.md for the
increment this landed in.

* **`SessionControls`** (`vscode-plugin/src/host/features/sessionControls/sessionControls.ts`)
  is a thin, typed layer over `AcpConnection`'s `setSessionMode()`/`extMethod()` calls, and the
  single place that coalesces every control-plane notification into one running
  `StatusSnapshot` (`model`, `thinkingEnabled`, `thinkingEffort`, `permissionMode`,
  `usedTokens`, `maxTokens`, `outputTokens`, `sessionTitle`, `workspaceTrusted`), broadcast to
  its constructor's `onStatus` callback — always the *complete* currently-known snapshot, not a
  delta, every time any one field changes. `applySessionInfo(info: SessionInfo)` (called once
  per `session/new`, via `KlorbSessionViewProvider.onSessionInfo()`) applies the session's
  starting mode/workspace-trust/title, resets the transient token tallies and the
  not-yet-fetched model/thinking fields to unknown, then kicks off a background
  `getSessionConfig()` fetch (logged and swallowed on failure) so the model chip populates
  promptly without the caller waiting on it.
  `applyModeChanged(modeId)`/`applySessionTitleChanged(title)`/`applyUsageUpdate(usedTokens,
  maxTokens, outputTokens)` are the parallel handlers for a pushed
  `current_mode_update`/`session_info_update`/`_klorb/usage`. `getSessionConfig()`/
  `setSessionConfig(update)` round-trip
  `_klorb/getSessionConfig`/`_klorb/setSessionConfig` (model selection and thinking
  enabled/effort ride one call each, mirroring the wire shape 1:1 — see [[klorb-server]]'s
  "Model and thinking session config" section); `setMode(modeId)` sends `session/set_mode` and
  applies the change eagerly (the server's own `current_mode_update` push then re-applies the
  same value, harmlessly); `cyclePermissionMode()` advances through the fixed
  `PERMISSION_MODE_CYCLE` (`ask -> auto -> deny -> ask`, mirroring
  `klorb.tui.constants.PERMISSION_FRAMEWORK_CYCLE`); `sessionStats()`/`trustWorkspace()`/
  `reloadSkills()` round-trip their own `_klorb/*` ext methods. `postSnapshot()` re-broadcasts
  the current snapshot unchanged — called by `KlorbSessionViewProvider.resolveWebviewView()` so
  a recreated webview sees real state instead of placeholder defaults, mirroring
  `KlorbAcpClient.repostPendingInteraction()`.
* **Webview `StatusRow`** (`src/webview/components/StatusRow.tsx`) is docked under the prompt
  input, one line (the session title itself lives in `App`'s top `.title` bar instead, see its
  own bullet above). Its leading chip is `StatusMenu` (`src/webview/components/StatusMenu.tsx`):
  a small solid `.status-menu-button` (`--vscode-button-background`/`-foreground`/
  `-hoverBackground`, the same primary-button palette `<vscode-button>` itself renders with,
  reproduced in plain CSS here since this is a bare `<button>` not the custom element) reading
  `^`, opening a `<vscode-context-menu>` listing every session command the row doesn't already
  expose as its own chip — Set Model, Set Thinking, Set Permission Mode, Session Stats, New
  Session, Reload Skills, and a task-panel item labeled by its current visibility ("Hide Task
  Panel"/"Show Task Panel", see `menuItems()` — this last one is the only recovery path once
  the task panel's own header pin has hidden it, since `TaskPanel` itself renders nothing at all
  while hidden). Picking an item dispatches to the same handler its own chip would use
  for the first two (`onPickModel`/`onPickThinking`), a dedicated `onSetPermissionMode` for the
  third (distinct from the badge's own `onCyclePermissionMode`, see below), posts one of
  `showSessionStats`/`newSession`/`reloadSkills` (webview → host) for the next three, or calls
  `onToggleTaskPanel` for the last one -- `App`'s own `toggleTaskPanelVisible()`, flipping local
  `taskPanelVisible` state directly with no host round trip at all, the same function the task
  panel's own pin already calls (see "Task panel" above). The popup
  is positioned with an inline `position: fixed` plus `top`/`left` computed from the chevron
  button's own `getBoundingClientRect()` (set imperatively in `openMenu()`, not through a CSS
  rule): `vscode-context-menu`'s own shadow-DOM styles set `:host { position: relative }`, which
  an ordinary page-level stylesheet rule can't reliably out-specificity, but an inline style on
  the host element always wins over any stylesheet — shadow-root or page — that doesn't mark
  itself `!important`; `position: fixed` also means the popup isn't clipped by any ancestor's
  layout or `overflow`, since its containing block is the whole webview viewport rather than
  `#status-row` or any element between it and the page root. The menu tracks its own open/closed
  state internally (outside click, Escape, item pick all close it without any React round trip);
  the button only ever calls `openMenu()` (which sets the popup's position and then
  `menuRef.current.show = true`) to open it, so there's no `open` boolean in React state to keep
  in sync with the element's own visibility. The model chip and the
  thinking chip are separate, independently clickable buttons, each opening its own picker: the
  model chip reads `model`, or `...` before the first
  `_klorb/getSessionConfig` reply, and clicking it posts `{type: 'pickModel'}`; the thinking chip
  reads the effort name (`Low`/`Medium`/`High`) while thinking is enabled, `Off` while disabled,
  or `...` before `thinkingEnabled`/`thinkingEffort` are known, and clicking it posts
  `{type: 'pickThinking'}` — this opens the same single `Off`/`Low`/`Medium`/`High` QuickPick
  **Klorb: Set Thinking** does (see its own bullet below), covering whether thinking is enabled
  and its effort level as one four-way choice rather than two separately-settable properties —
  see docs/adrs/merge-thinking-enabled-and-effort-into-one-picker.md for why. The permission
  badge reads `[ask]`/`[auto]`/`[deny]`, defaulting to `[ask]` before any mode is known; clicking
  it posts `{type: 'cyclePermissionMode'}`; a brief
  `.status-permission-badge-flash` CSS-transition highlight plays on every change (its own
  `useEffect`, keyed on `permissionMode`, comparing against the previous render's value via a
  `useRef`). The token tally renders `formatTokenCount.ts`'s SI-suffixed `↑ <used> / <limit>`
  and `↓ <output>` (2 significant figures once >= 1000, mirroring
  `klorb.tui.formatting.format_token_count` one-for-one — see that module's own docstring for
  the rounding rule, and `klorb.tui.mixins.status_bar`'s own two footer tallies for the TUI
  parity this mirrors) space-joined into one chip, omitting `/ <limit>` when `maxTokens` is
  `null`, either half until its own count is known, and the whole chip until at least one count
  is known.
* **Message protocol.** `StatusUpdateMessage` (host → webview, `shared/webviewMessages.ts`) is
  `{type: 'statusUpdate', model?, thinkingEnabled?, thinkingEffort?, permissionMode?,
  usedTokens?, maxTokens?, outputTokens?, sessionTitle?, workspaceTrusted?}` — every field
  independently optional, since the host posts one whenever any single piece of
  `SessionControls`'s snapshot changes but always with the complete snapshot (see `App`'s own
  bullet above for why the webview replaces its local `status` wholesale rather than merging).
  `PickModelMessage`/`PickThinkingMessage`/`CyclePermissionModeMessage`/
  `SetPermissionModeMessage`/`ShowSessionStatsMessage`/`NewSessionMessage`/`ReloadSkillsMessage`
  (webview → host) are the seven bare `{type: 'pickModel'}`/`{type: 'pickThinking'}`/
  `{type: 'cyclePermissionMode'}`/`{type: 'setPermissionMode'}`/`{type: 'showSessionStats'}`/
  `{type: 'newSession'}`/`{type: 'reloadSkills'}` intents the status row's clickable chips (and
  the prompt input's Shift+Tab handler, for `cyclePermissionMode`) post.
  `KlorbSessionViewProvider._handleMessage()` maps each one to the same
  `klorb.selectModel`/`klorb.setThinking`/`klorb.cyclePermissionMode`/`klorb.setPermissionMode`/
  `klorb.showSessionStats`/`klorb.newSession`/`klorb.reloadSkills` command the command palette
  itself runs, rather than duplicating their `QuickPick`/handler logic.
  `SessionStatsMessage` (host → webview) is `{type: 'sessionStats', messageCounts:
  Record<string, number>, toolBreakdown: {name, succeeded, failed}[], tokenUsage: Record<string,
  number>, cachePercent: number, totalCost: number}` — posted once per `klorb.showSessionStats`
  invocation (not a coalesced/replayed snapshot like `StatusUpdateMessage`, since each stats
  request is its own permanent history entry, not ongoing state).
* **Native pickers and commands** (`src/host/features/sessionControls/commands.ts`) use
  `vscode.window.showQuickPick` — not webview panels — for infrequent control changes; each
  handler takes `SessionControls` plus an injected `CommandsVsCode` facade (the same "keep the
  real side-effecting API behind an injectable seam" shape `EditorIntegration`'s
  `EditorIntegrationVsCode` uses, so this module loads under `vitest` without a running
  extension host) built once by `extension.ts`'s `realCommandsVsCode()`.
  * **`klorb.selectModel`**: QuickPick of `getSessionConfig().model.available`, current one
    marked `"current"`; on pick, `setSessionConfig({model: pickedId})`.
  * **`klorb.setThinking`**: `pickThinkingState()`'s single `Off`/`Low`/`Medium`/`High`
    QuickPick (current choice marked `"current"` — `Off` when disabled, the effort name when
    enabled). Picking `Off` calls `setSessionConfig({thinking: {enabled: false}})`; picking an
    effort calls `setSessionConfig({thinking: {enabled: true, effort}})` — always sending both
    fields together, so there's no path that changes `effort` while silently leaving `enabled`
    at whatever it previously was (see docs/adrs/merge-thinking-enabled-and-effort-into-one-
    picker.md).
  * **`klorb.cyclePermissionMode`** (also reachable from the status row badge): calls
    `SessionControls.cyclePermissionMode()`.
  * **`klorb.setPermissionMode`** (also reachable from the status row's `StatusMenu`): a QuickPick
    of `Ask`/`Auto`/`Deny` (`PERMISSION_MODE_CYCLE`'s three values, current one read from
    `SessionControls.currentModeId` and marked `"current"`); on pick, calls
    `SessionControls.setMode(pickedModeId)` directly, jumping straight to the chosen mode rather
    than only advancing one step through the cycle the way `cyclePermissionMode` does.
  * **`klorb.newSession`**: see the `extension.ts` bullet above.
  * **`klorb.showSessionStats`**: `formatSessionStats()`
    (`src/host/features/sessionControls/formatSessionStats.ts`) extracts a `_klorb/
    sessionStats` result's snake_case fields (see [[klorb-server]]'s extension-methods
    section) into the structured `SessionStatsData` shape (`messageCounts`/`tokenUsage`
    ordered label -> value maps, a sorted `toolBreakdown`, `cachePercent`, `totalCost` — see
    docs/specs/klorb-server.md's own derivation for `cache_pct`/`uncached_tokens`/
    `in_out_tokens`, which this mirrors) — no string formatting at this layer. `vs.
    postSessionStats(data)` posts it to the webview as a `sessionStats` message, which
    `historyModel.ts`'s `applyHostMessage()` appends as a `SessionStatsHistoryEntry`, rendered
    by `SessionStatsCard` (`src/webview/features/history/components/SessionStatsCard.tsx`) as
    a permanent history entry — not a toast, and not dumped to the output channel. The card
    lays out message/tool-call counts, token usage, and the total cost as one shared
    `.session-stats-grid` CSS grid (five columns: `max-content` label, a `1fr` spacer for
    breathing room, `max-content` right-aligned comma-grouped value, `max-content` note, and a
    trailing `2fr` spacer that keeps the numbers from spreading to the panel's far edge)
    rather than several independently-sized tables, specifically so every section's value
    column lands in the same place — a section title or the "Per-tool breakdown" subtitle is a
    grid item spanning every column, which is what lets titles and differently-shaped rows
    interleave with the aligned numeric rows in one flat DOM list (`StatRow` returns its
    label/spacer/value/note/spacer as a bare `<>` fragment of five plain `<span>`s for exactly
    this reason — see the component's own doc comment). A rule marking where the summary rows
    start sits between "Output tokens" and "Total tokens": four individually auto-placed,
    bordered `.session-stats-rule-cell`s plus one plain, unbordered `<span>` for the trailing
    spacer column — every row in this grid, without exception, consumes exactly five
    auto-placed cells; a single `grid-column`-spanning div in place of those five would leave
    its row's last column free for the *next* auto-placed item to slide into, desyncing every
    row after it from the grid's five columns (this is exactly the rendering bug an earlier
    version of this rule had — see the component's own inline comment where it's built). The
    "Cached tokens" row's own dim `(<cachePercent>%)` note is the only row using that column.
    The stat-name column uses the
    panel's regular UI font (`.session-stats-label`); everything else keeps the card's
    monospace font for tabular-looking numbers. This is the
    same layout `klorb.session_statistics.SessionStatistics.format_report()` renders as
    monospace text for the TUI, reimplemented as a real grid instead of preformatted lines.
  * **`klorb.reloadSkills`**: calls `SessionControls.reloadSkills()`, toasts the resulting
    skill count.
  * All nine are contributed in `package.json` with "Klorb: …" titles.
* **Workspace trust bridging** (`src/host/features/sessionControls/workspaceTrustBridge.ts`'s
  `WorkspaceTrustBridge`) offers, at most once per activation, to trust the session's workspace
  in Klorb: if VS Code's own workspace trust (`vscode.workspace.isTrusted`) is already granted
  but `SessionControls.workspaceTrusted === false`, `offerIfNeeded()` (called once right after
  `AcpConnection.start()` resolves, and again after `klorb.restartServer`) shows an information
  message ("Trust this workspace in Klorb? …") with Trust/Not now, calling
  `SessionControls.trustWorkspace()` on accept. If VS Code itself is in Restricted Mode at that
  point, `offerIfNeeded()` no-ops instead of offering; the constructor's own
  `vscode.workspace.onDidGrantWorkspaceTrust` subscription re-invokes `offerIfNeeded()` once VS
  Code's own trust is later granted, which is what lets the offer eventually happen in that
  case. An `_offered` flag (not a re-checkable predicate) guarantees at most one prompt per
  activation regardless of how many times `offerIfNeeded()` is called. Every call site invokes
  `offerIfNeeded()` fire-and-forget (`void`), so a failure inside it (most plausibly
  `trustWorkspace()`'s ACP round trip) is caught internally and logged via its own `LogFn`
  (defaulting to `console.error`, following the same pattern as `AcpConnection`/`KlorbAcpClient`)
  rather than becoming an unhandled promise rejection with no klorb-specific context at all.
* **OpenRouter API key storage** (`src/host/apiKeyStorage.ts`'s `ApiKeyManager`) stores the key
  in VS Code's OS-keychain-backed `vscode.ExtensionContext.secrets` (`SecretStorage`), keyed
  `klorb.openRouterApiKey`. `resolve()` — called from `extension.ts`'s `readServerOptions()`,
  threaded into the child's `OPENROUTER_API_KEY` environment variable when defined — returns the
  stored secret, or `undefined` (rather than an empty string) when none is set so an
  already-exported `OPENROUTER_API_KEY` in the shell that launched VS Code passes through to
  the child unchanged. `setApiKeyCommand()` (`klorb.setOpenRouterApiKey`) prompts via
  `showInputBox({password: true})` and stores the value, or deletes the stored secret on an
  empty submission; `clearApiKeyCommand()` (`klorb.clearOpenRouterApiKey`) deletes it
  explicitly.

### Task panel

A collapsible strip docked at the top of the sidebar, above `HistoryView`, rendering
[[klorb-server]]'s chainlink-backed `plan` `session/update`s -- the tall-narrow adaptation of the
TUI's Ctrl+T right-hand sidebar. Unlike the TUI, the panel has **no client-side chainlink access
of its own**: the server's `plan` snapshot is the only source of task data, so the panel can only
ever show what the server last sent, never fetch or refresh independently.

* **Flattening** (`KlorbAcpClient.sessionUpdate()`'s `'plan'` case, `src/host/features/acp/
  klorbAcpClient.ts`) maps an ACP `plan` update onto a `TaskListUpdateMessage`: `taskInfoFromEntry()`
  flattens each `PlanEntry`, preferring its own `_meta.klorb` detail (`issueId`,
  `openBlockerCount`, `closed`, `isCurrentTask` — see [[klorb-server]]'s "Chainlink task-plan
  updates" section) when present; `taskListSummary()` does the same for the update-level
  `_meta.klorb` counts (`openCount`/`closedCount`/`blockedCount`/`currentTaskId`). Both fall back
  to deriving the same fields from `status`/`tasks` alone when that `_meta.klorb` detail is
  absent — the degraded path for a stock (non-klorb) ACP agent's plain `PlanEntry`/`Plan`, which
  reports every status correctly but no `issueId` and no blocked-ness at all (there being nothing
  in the standard schema to derive it from). klorb's own server always attaches the detail, so
  this degrade path only matters against a non-klorb peer.
* **`TaskListUpdateMessage`** (`src/shared/webviewMessages.ts`) is `{type: 'taskListUpdate',
  summary: TaskListSummary, tasks: TaskInfo[]}` — `TaskListSummary` is `{openCount, closedCount,
  blockedCount, currentTaskId: number | null}`; `TaskInfo` is `{issueId?: number, text: string,
  priority: string, status: string, blocked: boolean, isCurrentTask: boolean, closed: boolean}`
  (`priority`/`status` are plain strings, mirroring `ToolCallStartedMessage.kind`, so a value
  this client doesn't recognize yet still round-trips). `KlorbSessionViewProvider.
  onTaskListUpdate()` (the `SessionUpdateListener` method `KlorbAcpClient` calls) posts the
  message to the webview as-is.
* **Webview model** (`webview/features/history/historyModel.ts`'s `TaskListSnapshot`/
  `applyTaskListUpdate()`, exported through that feature's barrel alongside the history-entry
  reducers even though the task list isn't a history entry — the same shared-reducer home
  `applyPendingInteraction()` already uses for non-entry panel state): `App`'s `taskList` state
  (a `TaskListSnapshot | undefined`) is replaced wholesale on every `taskListUpdate` message —
  the server always sends every task, never a delta — and cleared on `sessionReset`, since a
  fresh session may not send an initial plan snapshot at all (see [[klorb-server]]'s
  `_maybe_send_initial_plan_snapshot` gate) and a stale prior session's tasks must not linger.
  `App` persists `taskList` via `vscode.setState()` alongside `entries`/`pendingInteraction`/
  `status`, and threads it through `main.tsx` as `initialTaskList`.
* **`TaskPanel`** (`src/webview/components/TaskPanel.tsx`, a top-level component, not part of the
  `history` feature) shows a static "No tasks available" summary line (no chevron, no expand
  affordance) while `taskList` is `undefined` — no plan update has arrived yet, so there's no
  client-side chainlink access to derive a real placeholder from, but the panel still renders
  something once the user has asked to see it rather than showing nothing at all. Once a plan
  update has arrived, it's a plain `<details>`/`<summary>`
  disclosure (the same hand-rolled pattern the thinking block and the approval panel's "Show full
  command" already use), collapsed by default:
  * The `<summary>` is always visible regardless of collapsed state, left to right: a
    `.task-panel-chevron` `chevron-right` `vscode-icon` (see below), a `checklist` `vscode-icon`,
    a one-line summary built from the snapshot's counts (`"Tasks: <openCount> open[ ·
    <blockedCount> blocked][ · #<currentTaskId> – <title>]"`, the bracketed clauses only present
    when there's something to report, `"Tasks: none"` for a plan update with zero entries), and a
    `pin` `vscode-icon` that hides the whole panel (see below). `TaskPanelSummaryText` renders the
    leading `"Tasks: <n> open"` clause in its own `<span>`, bold (`.task-panel-summary-headline`,
    `font-weight: 600`) whenever `openCount !== 0`; the trailing blocked-count/current-task
    clauses stay normal weight in the same `<span>`'s sibling text -- rendering this as a real
    JSX fragment rather than one plain string is what makes the mixed weight possible.
    `currentTaskLabel()` builds the `"#<id> – <title>"` clause by looking up `currentTaskId` in
    `tasks` and splitting its own `text` (already formatted `"#<id> <title>"` server-side — see
    [[klorb-server]]'s "Chainlink task-plan updates" section) on the first space, rather than
    re-deriving the title; it falls back to the bare `"#<id>"` if `currentTaskId` names no entry
    in `tasks` at all (defensive against a server inconsistency). The summary text itself never
    wraps and ellipsizes instead (`.task-panel-summary-text`'s `overflow: hidden; text-overflow:
    ellipsis; white-space: nowrap`, plus `min-width: 0` -- a flex item's default `min-width: auto`
    otherwise overrides `text-overflow` and lets the row grow instead of truncating). Click/Enter
    on the `<summary>` toggles the disclosure open/closed for free (native `<details>` behavior);
    this open/closed state is **not** persisted, mirroring the history's own "expand all tool
    calls" toggle. The chevron itself is a fixed `chevron-right` glyph that CSS rotates 90° (to
    point down) once the panel is open (`.task-panel[open] > .task-panel-summary
    .task-panel-chevron`), rather than swapping to a different icon name — it's what actually
    signals expand/collapse state at all, since `.task-panel-summary`'s own `display: flex`
    (needed for the chevron/icon/text/pin row layout) suppresses the native `<details>` marker
    triangle as a side effect.
  * Expanded, the body is a `max-height: 40vh`, independently-scrolling list (`.task-panel-list`)
    of every task in the order the server sent them: the current task gets a leading `★`, a closed
    task is dimmed and struck through (`.task-row-closed`), and a blocked task gets a dim
    `" (blocked)"` suffix — the TUI sidebar's exact visual grammar, restyled. Every row reserves
    a fixed-width `.task-star` column regardless of whether it's actually starred (an empty
    `<span>` when not), so a starred row's title isn't indented relative to every other row's.
    Keyed by `issueId` when known, else by index (a stock-ACP-agent plan reports no `issueId` at
    all).
* **Visibility toggle.** The panel's shown/hidden state (distinct from the disclosure's own
  expanded/collapsed state above) is `App`'s `taskPanelVisible` boolean, persisted the same way as
  `taskList`, threaded through `main.tsx` as `initialTaskPanelVisible`, and defaulting to `true`.
  The **Klorb: Toggle Task Panel** command (`klorb.toggleTaskPanel`, registered directly in
  `extension.ts` alongside `klorb.newSession`/`klorb.restartServer`, since it needs no
  `SessionControls`/ACP round trip at all) posts a bare `{type: 'toggleTaskPanel'}` host message,
  which `App` flips its own state on; clicking `TaskPanel`'s own header pin icon calls the exact
  same toggle handler directly (`event.preventDefault()` on the icon's click first, so hiding the
  panel doesn't also toggle the `<summary>`'s own disclosure open). When hidden, `TaskPanel` isn't
  rendered at all (including its own pin), so once hidden, the status row's `StatusMenu` (see
  "Status row and session controls" below) is the only UI element left to bring it back -- its
  own task-panel item calls `toggleTaskPanelVisible()` directly too, with no host round trip,
  and its label reflects current visibility so picking it always reads as the right action --
  `taskPanelVisible` alone is an accurate stand-in for what's on screen, since `TaskPanel` always
  renders something once it's mounted (see its own doc comment above for the "No tasks available"
  placeholder), never silently nothing.

### Subagents panel

A strip docked at the top of the sidebar, above `TaskPanel` (which docks above `HistoryView` --
see "Task panel" above), listing every session in the tree rooted at the root session -- the
VSCode adaptation of the TUI's Ctrl+G right-hand panel
(`klorb.tui.widgets.subagents_panel.SubagentsPanel`, see docs/specs/subagents.md's "Subagents
panel (TUI)" section). Unlike the TUI, which holds every `Session` object directly in-process,
the webview never speaks ACP itself (`docs/adrs/vscode-webview-stays-acp-ignorant-behind-typed-
messages.md`), so this feature is built on three new `_klorb/*` ext methods
([[klorb-server]]'s own "Extension methods" section documents the wire contract in full):
`_klorb/subagentTree` (a snapshot of the whole tree), `_klorb/subagentTranscript` (one
subagent's message history), and `_klorb/subagentCancel` (per-subagent Stop). None of the three
push unprompted -- a subagent's turn never streams at all (see docs/specs/subagents.md's
"Security model" section) and nothing wakes an idle creator when one finishes (that section's
"Out of scope" also covers the ACP layer) -- so this feature is poll-driven end to end, unlike
every other host↔webview data flow in this document, which rides an ACP push.

* **`SubagentPoller`** (`src/host/features/subagents/subagentPoller.ts`) owns two independent
  `setInterval` timers against the live `AcpConnection`: a tree poll (`_klorb/subagentTree`,
  every 2s) runs whenever the webview's panel is visible (`setPanelVisible(true)`); a transcript
  poll (`_klorb/subagentTranscript`, every 1s, plus one immediate off-interval fetch on
  selection so the view doesn't sit empty for up to a second) additionally requires a non-root
  selection (`selectSubagent(sessionId)`). Both timers are no-ops (never even start) unless
  `AcpConnection.subagentsCapable` is `true` -- the connection's own `initialize()`-negotiated
  `agentCapabilities._meta.klorb.subagents` flag, threaded through `SessionInfo`/
  `StatusSnapshot.subagentsCapable` the same way `enqueueMessageCapable` already is, so an older
  server that predates this feature isn't hit with a stream of `method not found` errors.
  `resync()` re-evaluates both timers against the connection's current `subagentsCapable`, with
  no change to the poller's own tracked visibility/selection state -- `extension.ts`'s
  `startConnection()` calls it right after `connection.start()` resolves, since the webview's own
  mount-effect resync message (below) can call `setPanelVisible(true)` (restoring a persisted
  `subagentsPanelVisible: true`) before that `initialize()` handshake finishes; without `resync()`,
  `setPanelVisible`'s own gate would see `subagentsCapable` still `false` at that moment and never
  start the timer, leaving the panel flagged open with nothing polling until the user manually
  toggled it closed and back open. `cancelSubagent(sessionId)` calls `_klorb/subagentCancel`
  directly (no timer involved).
  `KlorbSessionViewProvider` owns one `SubagentPoller` instance (constructed in `extension.ts`
  alongside `SessionControls`) and routes the webview's `setSubagentsPanelVisible`/
  `selectSubagent`/`cancelSubagent` messages into it; its two callbacks post
  `subagentTreeUpdate`/`subagentTranscriptUpdate` host messages back, mirroring how
  `SessionControls`'s own status callback posts `statusUpdate`.
* **`SubagentNodeInfo`** (`src/shared/webviewMessages.ts`) is the wire shape for one tree node --
  `{id, parentId, address, title, role, state: "running" | "finished" | null, aborted, model,
  thinkingEnabled, thinkingEffort, usedTokens, maxTokens, outputTokens}` -- the same fields
  [[klorb-server]]'s `_klorb/subagentTree` result documents; the root session is node `parentId:
  null`, always present, so the panel and every selection-following piece of chrome (below) treat
  root and subagent selection uniformly instead of as two separate concepts. `SubagentTreeUpdateMessage`/
  `SubagentTranscriptUpdateMessage` (host → webview) and `SetSubagentsPanelVisibleMessage`/
  `SelectSubagentMessage`/`CancelSubagentMessage`/`ToggleSubagentsPanelMessage` (webview ↔ host)
  round out the protocol; `webview/features/subagents/subagentsModel.ts` holds the pure reducers
  (`applySubagentTreeUpdate`/`applySubagentTranscriptUpdate`, replace-wholesale-or-clear-on-
  sessionReset, mirroring `applyTaskListUpdate`) and `subagentTranscriptEntries()`, which converts
  a transcript snapshot's wire entries into `HistoryEntry[]` for `HistoryView` reuse (see below)
  and overrides each `toolCall` entry's `expanded` flag from a separately-tracked
  `expandedCallIds` set -- the wire's own `expanded` is always `false` (`_replay_tool_call_entry`
  never persists it), so without this override a user's own expand/collapse toggle would revert
  on the very next 1s poll.
* **`SubagentsPanel`** (`src/webview/features/subagents/components/SubagentsPanel.tsx`) renders
  nothing until the first `subagentTreeUpdate` arrives (mirroring `TaskPanel`), then one button
  row per node in tree order -- **no depth-based indentation** (a node's dotted-decimal address
  already reads as nested by virtue of being longer than its parent's) and **one leading marker
  slot**, not one per state: `rowMarker()` (`subagentsModel.ts`) returns `"!"` only while the
  panel's own 600ms blink timer (`useBlinkPhase`, mirroring the TUI's `_PANEL_TICK_INTERVAL_SECONDS`)
  is in its on-phase *and* the row is the session an outstanding ask belongs to, else `"*"` for a
  running row, else nothing -- exactly `SubagentsPanel._render_row_label`'s own precedence.
  Selection is click-only (native `<button>` Tab/Enter/Space activation), a deliberate scope
  reduction from the TUI's `OptionList`-driven arrow-key roving selection. The footer shows the
  selected row's role.
* **Selection is global, including the root session**, exactly mirroring the TUI's own "Selection
  is global" rule (docs/specs/subagents.md's "Subagents panel (TUI)" section): `App`'s
  `selectedSubagentId: string | null` (`null` = root) is the one piece of state every other
  selection-dependent piece of chrome below reads.
  * **The transcript view** (`SubagentTranscriptView.tsx`) reuses `HistoryView` wholesale against
    `subagentTranscriptEntries(...)` rather than a second render path -- deliberately, since a
    bug in the TUI's own first-cut `_mount_subagent_messages` (a `tool_use` message's own
    `content` alongside its tool calls, and a `thinking` message's `reasoning_details` fallback,
    both silently dropped) turned out to live in the *data-building* layer
    (`klorb.server.update_mapping.build_session_replay`, shared by `_klorb/sessionReplay` and
    `_klorb/subagentTranscript` alike), not the render layer -- fixing it once there fixes both
    the root session's own saved-session restore and every subagent transcript, for free, rather
    than needing a parallel fix in a from-scratch VSCode render path. Own pin-to-bottom tracking
    (`webview/hooks/usePinnedScroll`, factored out of `App.tsx`'s own root-history scroll-pin
    logic once this became the second call site needing it), independent of the root `#history`
    view's. A trailing status line reports one of four states -- "Subagent is still working…" /
    "Subagent task complete." / "Subagent interrupted." / "Sending interrupt…" (the last shown
    immediately on a Stop click, via `App`'s own `subagentInterruptPending` state, until a poll
    confirms `state: "finished"`) -- matching the TUI's own four-state notice one-for-one.
  * **The prompt input is disabled whenever a subagent is selected** (`PromptInput`'s `readOnly`
    prop hides the textarea and Send button entirely) -- the user cannot address a subagent
    directly. The Stop button is unaffected by `readOnly`: while a subagent is selected, `App`
    wires `inFlight`/`onCancel` to that subagent's own running state/`cancelSubagentTurn()`
    instead of the root session's, so Stop still cancels whichever turn is actually on screen.
  * **The status row's model/thinking chips follow the selection** and go non-interactive:
    `StatusRow`'s `interactive` prop (`false` while a subagent is selected) swaps the model/
    thinking chips from `<button>` to plain `<span>` -- a subagent's `SessionConfig` is fixed at
    creation (docs/specs/subagents.md's "Subagent session model" section), so there is nothing a
    click could change. `App`'s `selectedStatusOverrides()` overlays the selected node's own
    `model`/`thinkingEnabled`/`thinkingEffort`/`usedTokens`/`maxTokens`/`outputTokens` on top of
    the root's own `StatusSnapshot` before it reaches `StatusRow`, so the token tally too always
    reflects whichever session is on screen -- the VSCode analogue of the TUI's `_selected_session`
    reads in `StatusBarMixin`/`ReplApp.format_title`. The permission-mode badge is *not* gated by
    `interactive`: `permission_framework` is shared by reference across the whole session tree
    (docs/specs/subagents.md's "Shared permission framework state" section), so cycling it while
    a subagent is selected is still a legitimate, tree-wide action.
  * **The panel header title follows the selection** (`App`'s `headerTitle`, the selected node's
    own address and title for a subagent, `sessionTitleText(status)` for the root) -- the VSCode
    analogue of the TUI's `#session-name` status line following `_selected_session.name`.
* **Ask routing.** A subagent-raised ask (`PermissionAskMessage`/`QuestionAskMessage`'s
  `originSessionId`, see [[klorb-server]]'s own doc comment on that field) is answerable in the
  interaction area only once its own session is the one selected (`App`'s `interactionVisible`) --
  the VSCode counterpart of the TUI's `_await_session_selected` gate, implemented client-side
  since (unlike the TUI) nothing here blocks a server-side lock waiting for a selection to
  change. While it isn't visible, the owning row shows the blinking `"!"` marker instead (via
  `attentionSessionId`, resolved from `originSessionId` to that node's own tree id -- the root's
  `originSessionId` is `undefined`, resolved to the root node's own `id` from the current tree
  snapshot so the marker can still target a real row). Because `KlorbAcpClient` only ever
  surfaces one interaction at a time (its own `_interactionBusy`/`_interactionQueue` serialize
  every ask system-wide, root or subagent alike -- see "Approval and question panels" above),
  `App` only needs one `pendingInteraction` slot, gated by whether its `originSessionId` matches
  the current selection, not a full per-session queue. **While the panel itself is hidden**, the
  blinking row marker obviously can't be seen at all, so a `#subagent-attention-fallback` bar
  (`showAttentionFallback`) attaches just above the prompt input instead -- "Agent `<address>`
  needs your input", clickable to open the panel and jump straight to that row -- mirroring the
  TUI's own `#subagent-attention-status` line (`SubagentsPanelMixin.
  _update_subagent_attention_status_line`), shown by the same rule: only while the panel is
  closed, never alongside the marker itself.
* **Visibility toggle** mirrors the task panel's own exactly (`App`'s `subagentsPanelVisible`,
  persisted via `vscode.setState()`, the **Klorb: Toggle Subagents Panel** command
  (`klorb.toggleSubagentsPanel`) posting a bare `{type: 'toggleSubagentsPanel'}`, and a
  `StatusMenu` item reflecting current visibility) -- with one addition Tasks doesn't need:
  toggling also posts `{type: 'setSubagentsPanelVisible', visible}` to the host so
  `SubagentPoller` starts/stops its tree timer. Because the poller's timers live in the extension
  host (which can outlive a webview reload) while the visibility/selection flags live in the
  webview's own persisted state, `App` re-sends both on every mount (keyed off its own
  `initialSubagentsPanelVisible`/`initialSelectedSubagentId` props, never off the corresponding
  live state, so the effect stays exhaustive-deps-correct without re-firing on every render) to
  keep the two sides reconciled after a reload.
* **Auto-opens on the session's first `CreateSubagent` call**, rather than requiring the user to
  notice a subagent exists and open the panel manually: `App`'s `onMessage` handler checks every
  `toolCallStarted` message's `toolName` field (`_meta.klorb.toolName`, [[klorb-server]]'s own
  "Tool-call update mapping" section) for `"CreateSubagent"` and calls `showSubagentsPanel()`,
  which sets `subagentsPanelVisible` (and posts `setSubagentsPanelVisible`) only when it isn't
  already `true` -- unlike `toggleSubagentsPanelVisible`, a second `CreateSubagent` call while
  the panel is already open doesn't flip it shut again.

### Webview message protocol

The webview and the extension host exchange messages shaped by the discriminated unions in
`vscode-plugin/src/shared/webviewMessages.ts` — one module included by both tsconfigs (host
and webview) so the same types check both sides — over the standard `vscode.postMessage()` /
`window.addEventListener('message', ...)` webview messaging channel. The webview never speaks
ACP directly; `KlorbSessionViewProvider` is the only place that translates between the two (see
`docs/adrs/vscode-webview-stays-acp-ignorant-behind-typed-messages.md`).

* Webview → host (`WebviewMessage`): `{type: 'submitPrompt', text: string, images?:
  ImageAttachment[]}` (once per submitted prompt while idle), `{type: 'enqueueMessage', text:
  string, images?: ImageAttachment[]}` (a mid-turn submit, "Queued messages and interrupt
  polish" above -- `images` here is rejected server-side, "Image attachments" above),
  `{type: 'cancelTurn'}` (Stop button or Escape while a
  turn is running), `{type: 'restartServer'}` (a `'serverError'` entry's action button, "Queued
  messages and interrupt polish" above), `{type: 'openLocation', path: string, line?: number}`
  (a tool-call title link), `{type: 'openDiff', callId: string, path: string}` ("Open diff"),
  `PermissionDecisionMessage` ("Approval and question panels" above) answering a
  `permissionAsk`, `QuestionAnswerMessage` ("Approval and question panels" above) answering a
  `questionAsk`, `{type: 'pickModel'}`/`{type: 'pickThinking'}`/
  `{type: 'cyclePermissionMode'}`/`{type: 'showSessionStats'}`/`{type: 'newSession'}`/
  `{type: 'reloadSkills'}` ("Status row and session controls" above, the status row's chips and
  its `StatusMenu` popup), `{type: 'attachImageFile'}` ("Image attachments" above, the status
  row's file-picker item), `{type: 'setSubagentsPanelVisible', visible: boolean}`/
  `{type: 'selectSubagent', sessionId: string | null}`/`{type: 'cancelSubagent', sessionId:
  string}` ("Subagents panel" above), and `WebviewErrorMessage`
  (`{type: 'webviewError', message: string, stack?: string}`, "Webview UI structure" above's
  `ErrorBoundary`).
* Host → webview (`HostMessage`): `{type: 'turnStarted'}`, `{type: 'agentChunk', text:
  string}`, `{type: 'thoughtChunk', text: string}`, `{type: 'turnEnded', stopReason: string}`,
  `{type: 'turnError', message: string}`, `{type: 'serverLost', message: string}` (a lost
  server process mid-turn, "Queued messages and interrupt polish" above),
  `{type: 'messageQueued', text: string}`/`{type: 'queuedMessageSent', text: string}` (both
  "Queued messages and interrupt polish" above), `{type: 'sessionReset'}`,
  `{type: 'toolCallStarted', callId, title, kind, locations, toolName?}`, and
  `{type: 'toolCallUpdated', callId, status, title?, contentText?, diff?, locations?}` — `kind`/
  `status` are plain strings (mirroring `turnEnded`'s own loosely-typed `stopReason`) so a value
  this client doesn't recognize yet still round-trips instead of failing to parse; `locations`
  is `{path: string, line?: number}[]`; `diff` is `{path, oldText: string | null, newText:
  string, hunks?}` — `oldText`/`newText` are always present (ACP's own convention, `oldText:
  null` for a brand-new file), `hunks` (`{lines: {kind: 'context' | 'add' | 'del', oldLineno:
  number | null, newLineno: number | null, text: string}[]}[]`) only when the server attached
  `_meta.klorb.diffHunks` (klorb's own server always does) and is preferred for rendering when
  present. `KlorbAcpClient.sessionUpdate()` builds these two messages from ACP's `tool_call`/
  `tool_call_update` session updates (see "Tool-call rendering and editor integration" above);
  klorb's own server never omits `tool_call`'s `kind`/`locations` or `tool_call_update`'s
  `status` (see [[klorb-server]]'s tool-call update mapping section), but the flattening
  defaults `kind`/`locations` to `'other'`/`[]` and `status` to `'completed'` when a peer ACP
  agent does, `PermissionAskMessage` ("Approval and question panels" above),
  `QuestionAskMessage` ("Approval and question panels" above, both now also carrying
  `originSessionId?: string`, "Subagents panel" above), `StatusUpdateMessage` (now also
  carrying `activeModelVision?: boolean`, "Image attachments" above, and
  `subagentsCapable?: boolean`, "Subagents panel" above), and
  `SessionStatsMessage` (both "Status row and session controls" above),
  `TaskListUpdateMessage`/`{type: 'toggleTaskPanel'}` (both "Task panel" above),
  `SubagentTreeUpdateMessage`/`SubagentTranscriptUpdateMessage`/
  `{type: 'toggleSubagentsPanel'}` (all "Subagents panel" above),
  `{type: 'workspaceFiles', files: string[]}` ("File finder" above), and
  `{type: 'imageAttached', image: ImageAttachment}` ("Image attachments" above, the status
  row's file-picker result).
* `parseHostMessage()`/`parseWebviewMessage()` are the type guards each side runs on every
  incoming payload before acting on it, since both `onDidReceiveMessage`'s argument (host side)
  and `MessageEvent.data` (webview side) are untyped `unknown`. The richer message types above
  (nested arrays/objects) are validated by dedicated guard functions; every other message type
  still goes through the original `FieldSpec`-driven `parseMessage()` (one required string
  field, or none).

### Build: two esbuild bundles, two typecheck-only tsconfigs

The extension host code (`src/extension.ts`, `src/host/**`) and the webview code
(`src/webview/**`, `src/shared/**`) run in two different JavaScript environments — the
extension host is a Node/CommonJS process with the `vscode` module and Node's
`child_process`/`stream` APIs available, the webview is a sandboxed `vscode-webview://` document
with neither — so they're built by two different pipelines, both `noEmit: true` (typecheck-only)
tsconfigs paired with their own `esbuild` bundle (see
`docs/adrs/bundle-extension-host-with-esbuild-not-tsc-emit.md` for why the host is bundled at
all, not just type-checked):

* `tsconfig.json` type-checks everything under `src/` *except* `src/webview/` (plus
  `test/host/**`, `test/shared/**`, `test/mockAgent.ts` — see "Test tree" below), with
  `module`/`moduleResolution` set to `nodenext` (matching the extension host's real CommonJS
  `require()` semantics) and `lib: ["ES2022"]` (no `DOM` — the host never runs in a browser).
  Its `paths` alias `host/*` to `src/host/*` and `shared/*` to `src/shared/*`.
  `@agentclientprotocol/sdk` is ESM-only, so `AcpConnection.start()` loads it with a dynamic
  `import()` rather than a top-level import — the only way a CommonJS module can consume a
  pure-ESM package; every other symbol the host imports from the SDK is type-only (erased at
  compile time, so it doesn't hit this restriction). `skipLibCheck: true` is set because the
  SDK's own shipped `.d.ts` re-exports its generated schema module in a way the compiler flags
  as an export-ambiguity error under this project's strict settings — a problem in the SDK's own
  type declarations, not in code this project controls, so it's skipped rather than routing
  every SDK type through a local re-export shim. `verbatimModuleSyntax` is deliberately *not*
  set here (unlike the webview config): it requires CommonJS-formatted files (this project has
  no `"type": "module"` in `package.json`) to use `import x = require(...)` syntax instead of
  plain `import`/`export`, which would fight the host's existing ESM-style source throughout.
  `esbuild src/extension.ts --bundle --platform=node --format=cjs --external:vscode` (the
  `build:extension` npm script) produces the actual `out/extension.js` VS Code `require()`s;
  `vscode` is the one import left external since the module only exists inside a running VS
  Code process, not on disk.
* `tsconfig.webview.json` type-checks `src/webview/**` and `src/shared/**` (plus
  `test/webview/**` — see "Test tree" below) with `jsx: "react-jsx"` (automatic JSX runtime),
  `module: "preserve"` + `moduleResolution: "bundler"` (the pairing TypeScript recommends when a
  bundler, not `tsc` itself, does the real compilation), `lib: ["ES2022", "DOM",
  "DOM.Iterable"]`, and `types: []` so ambient `@types/node` globals aren't pulled into
  browser-only code. Its `paths` alias `webview/*` to `src/webview/*` and `shared/*` to
  `src/shared/*` — deliberately no `host/*` here, so the webview can't accidentally import
  extension-host-only code. `skipLibCheck: true` is set for the same SDK-declaration reason as
  the host config, though the webview tsconfig doesn't import the SDK itself. It also includes
  `vscode-plugin/types/*.d.ts` — the vendored vscode-elements JSX declarations (see "Component
  library" below) — so `<vscode-button>` etc. type-check as JSX intrinsics.
  `build-webview.mjs` (invoked by the `build:webview`/`build:webview:prod` npm scripts as `node
  build-webview.mjs`/`node build-webview.mjs --prod`) bundles `src/webview/main.tsx` into one
  self-contained `out/webview/main.js` with the webview's runtime dependencies (React,
  `react-markdown` and its remark plugins, `@vscode-elements/elements`, ...) all inlined
  alongside the plugin's own webview code, the same
  `--bundle --tsconfig=tsconfig.webview.json --format=iife --platform=browser --target=es2022`
  options a direct `esbuild` CLI invocation would use. It calls esbuild's JS API rather than its
  CLI specifically so it can register `esbuild-plugin-babel` as a plugin (the CLI has no
  plugin-registration mechanism): every `.ts`/`.tsx` file under `src/webview/**`/`src/shared/**`
  is routed through `@babel/preset-typescript` → `@babel/preset-react` (`runtime: 'automatic'`)
  → `babel-plugin-react-compiler` (`target: '19'`) before esbuild ever bundles it, so components
  get the compiler's automatic `useMemo`/`useCallback`-equivalent memoization
  (`useMemoCache`-backed) without being hand-written throughout — see
  `docs/adrs/run-react-compiler-through-babel-esbuild-plugin.md` for why this needed a Babel
  pass at all and why the plugin's `filter` matches only those two source directories rather than
  every file esbuild loads (avoiding running the same presets over inlined `node_modules`
  dependency source). `--define:process.env.NODE_ENV` (`"development"` unminified for
  `build:webview`, `"production"` minified with `--sourcemap=linked
  --sources-content=false --legal-comments=linked` for `build:webview:prod`) and the choice to
  otherwise skip `--minify` in the dev build are unchanged from the original stub (see
  `docs/adrs/use-react-for-the-webview-ui.md`); `docs/adrs/bundle-webview-script-with-esbuild-not-es-modules.md`
  explains why the output loads as a plain `<script nonce="...">` rather than
  `<script type="module">`. `src/webview/tsconfig.json` and `test/webview/tsconfig.json` are
  pointer files (`{"extends": "../../tsconfig.webview.json"}`) that exist purely so VS Code's
  editor tooling — which only auto-discovers a file literally named `tsconfig.json` by walking
  up from the open file — picks `tsconfig.webview.json`'s settings for files under those
  subtrees instead of falling back to the host `tsconfig.json` (which excludes them) with no
  `paths` aliases at all; no script ever invokes these two files directly.
* Both configs' `paths` aliases are what let source anywhere in `vscode-plugin/src/` use rooted
  imports (`shared/webviewMessages`, `webview/App`, `host/klorbServerProcess`, ...) instead of
  relative `../../` chains — see `AGENTS.md`'s "vscode-plugin source tree" section for the full
  convention (including the one exception: relative imports within the same `features/<name>/`
  folder). `esbuild` resolves and inlines these at bundle time regardless of what Node's own
  `require()` resolution would do with the same bare specifier — the reason the host needs
  bundling in the first place (see the ADR referenced above).
* Typechecking (`npm run typecheck`, i.e. `make typecheck`) runs `tsgo -p ./` and
  `tsgo -p tsconfig.webview.json` — `tsgo` (the `@typescript/native-preview` package's native,
  Go-ported compiler) rather than classic `tsc`. Linting still resolves against a real,
  non-prerelease `typescript` (`^6.0.3`), since `typescript-eslint` doesn't yet support the
  TypeScript 7.x line `tsgo` tracks; `@typescript-eslint/parser` is pinned as its own explicit
  devDependency so it hoists to the top-level `node_modules` (`eslint-plugin-import-x`'s
  cross-module parsing `require()`s it directly, and won't find a copy nested only inside the
  `typescript-eslint` meta-package's own `node_modules`). See
  `docs/adrs/vscode-plugin-typechecks-with-tsgo-lints-with-typescript-6.md`.

### Test tree

`vscode-plugin/test/` mirrors `vscode-plugin/src/` file-for-file (`test/host/`, `test/webview/`,
`test/shared/`, including the `features/` nesting) and resolves the same rooted aliases as
application code, via `vitest.config.mts`'s `vite-tsconfig-paths` plugin (pointed at both
`tsconfig.json` and `tsconfig.webview.json` via its `projects` option, since neither config's own
`include` would otherwise cover `test/`). `test/mockAgent.ts` is the one top-level exception —
a shared test helper, not a mirror of any single `src/` file, analogous to a `conftest.py`.
Because `vite-tsconfig-paths` only resolves aliases for importers a tsconfig's own `include`
covers, adding a new test subtree also means adding its glob to the matching tsconfig's
`include` (see each tsconfig's own comments) — otherwise the aliases silently fail to resolve
for tests rooted there, even though application code resolves them fine.

`vitest` (`make test`) transpiles TypeScript itself (via `vitest.config.mts`'s
`esbuild.jsx: 'automatic'` setting, matching `tsconfig.webview.json`'s JSX mode) independent of
either `esbuild` bundle, so it imports source modules directly rather than the built bundle.

### Component library

The webview's interactive controls (`<vscode-textarea>`, `<vscode-button>`, `<vscode-icon>`,
`<vscode-progress-ring>`, `<vscode-badge>`, `<vscode-textfield>`, and later increments' selects)
are `@vscode-elements/elements` custom elements, rendered directly from React 19 JSX with no
wrapper package — React 19 passes JSX props straight through to custom elements'
properties/attributes; `<details>`/`<summary>` (plain HTML, not a vscode-elements component) is
the disclosure used for the thinking block, the approval panel's "Show full command", and the
approval/question panels' "Other…" redirects. Their TypeScript JSX typings
live in `vscode-plugin/types/global.d.ts`, vendored from the vscode-elements examples repo's
own `global.d.ts` (declaring the `react`-module `JSX.IntrinsicElements` additions for every
element the library ships) rather than hand-written per element as they're adopted. See
`docs/adrs/use-vscode-elements-for-webview-controls.md` for why this library over the
alternatives.

`<vscode-icon>` renders a [Codicon](https://microsoft.github.io/vscode-codicons/dist/codicon.html)
glyph via a `codicon codicon-<name>` class and the `codicon` web font — that font is not built
into `@vscode-elements/elements` itself (`vscode-icon.js`'s `_getStylesheetConfig()` looks for a
page-level `<link id="vscode-codicon-stylesheet">` and warns, rendering an empty glyph, if it
can't find one). `codicon.css`/`codicon.ttf` are not committed to the repo: the `copy:codicons`
npm script (`mkdir -p out/media && cp node_modules/@vscode/codicons/dist/{codicon.css,codicon.ttf}
out/media/`, chained into `compile`/`compile:prod` ahead of both esbuild steps) copies them out of
the `@vscode/codicons` package into `out/media/` at build time, the same generated, git-ignored
location `out/extension.js`/`out/webview/main.js` already live in.
`KlorbSessionViewProvider._getHtml()` links `out/media/codicon.css` with that exact `id` before
`main.css`'s own `<link>` (`main.css` itself stays hand-maintained and committed under the source
`media/` directory, which this build step never touches). The CSP's `font-src
${webview.cspSource}` directive is what lets `codicon.css`'s own relative `@font-face { src:
url("./codicon.ttf...") }` load at all (`default-src 'none'` doesn't cover fonts by itself). This
deliberately does *not* follow `types/global.d.ts`'s "vendor a copy into the repo" precedent —
that file is a hand-adjustable, dev/typecheck-only artifact never shipped in the package at all
(`.vscodeignore` excludes `types/**`), whereas `codicon.css`/`.ttf` are unmodified binary/generated
assets that ship as-is inside the `.vsix`; committing a copy would drift from the version actually
pinned in `package.json` and bloat the repo's history on every codicon update, so deriving them at
build time from the pinned package is the better fit here. `@vscode/codicons` is a `dependencies`
entry (not `devDependencies`), matching the other webview runtime packages' (React,
`react-markdown`, `@vscode-elements/elements`, ...) own placement: this project's
`dependencies` list is "whatever's content ends up in the shipped output" (those are
esbuild-inlined into the bundle rather than resolved from `node_modules` at runtime, but their
code still ships), not narrowly "resolved via `require()`/`import()` at runtime" —
`@vscode/codicons`' files ship into the `.vsix` the same way, just via a copy step instead of a
bundler.

Markdown responses render via `react-markdown`, chosen over `marked` + `innerHTML` because it
renders to React elements without `dangerouslySetInnerHTML` — relevant since the rendered text
is model-generated and the webview runs under a CSP-locked `vscode-webview://` origin.
`HistoryView`'s `<ReactMarkdown>` passes `remarkPlugins={[remarkGfm, remarkFrontmatter]}`:
`remark-gfm` adds GitHub-flavored table/strikethrough/task-list/autolink parsing (rendered
`<table>`s are styled by `main.css`'s `.entry-response table/th/td` rules, since they'd otherwise
inherit no borders from the surrounding theme), and `remark-frontmatter` recognizes a leading
`---`-delimited YAML block as its own mdast `yaml` node instead of letting the base parser
misread it as a thematic break followed by a mangled paragraph.

`mdast-util-to-hast` drops `yaml`/`toml` nodes by default (no built-in handler renders them), so
`HistoryView` also passes `remarkRehypeOptions={{ handlers: { yaml: renderYamlFrontmatter } }}` —
`renderYamlFrontmatter` (`webview/features/history/renderYamlFrontmatter.ts`) parses the block's
raw text with the `yaml` package and renders the result as a two-column key/value `<table>`
(`.frontmatter-table`/`.frontmatter-key`/`.frontmatter-value`, styled alongside the GFM table
rules above): a nested mapping becomes a nested `.frontmatter-table`, and an array value becomes
a `.frontmatter-array` of stacked `.frontmatter-array-item` `<div>`s (one per element) rather
than another table, since array elements have no natural column headers. A block that fails to
parse as YAML is omitted from the rendered output, the same as an unhandled node type would be.

## Build tooling

`vscode-plugin/Makefile` mirrors `klorb/Makefile`'s target names, mapped onto the npm/VS Code
toolchain in place of `pip`/`uv`. The canonical command lines live in `package.json`'s
`scripts`, not the `Makefile`: `make lint`/`typecheck`/`test`/`compile` each just run the
matching `npm run <script>`, so there's one place to change a build/lint/test invocation instead
of two.

* `sync_deps` runs `npm install`, resolving `package.json`'s version ranges into
  `package-lock.json` — the npm analog of `uv pip compile` recomputing
  `dev-requirements.txt`/`release-requirements.txt`.
* `install_deps` (`npm ci --omit=dev`) and `install_dev_deps` (`npm ci`) install exactly what's
  pinned in `package-lock.json`, matching `klorb/Makefile`'s split between a runtime-only
  install and one that also brings in lint/typecheck/test tooling. `package.json`'s
  `dependencies` are `@agentclientprotocol/sdk` (the extension host `require()`s/`import()`s it
  at runtime, unlike the bundled webview deps — the plugin's first true runtime dependency) and
  the webview's own runtime packages (`@vscode-elements/elements`, `react`, `react-markdown` and
  its remark plugins, ...) — the latter are runtime dependencies of the *webview*, not the host,
  but `esbuild` inlines them into
  `out/webview/main.js` at build time rather than the packaged extension `require()`-ing a
  separate `node_modules` copy at runtime, so from `vsce`/`npm ci`'s point of view they still
  need to be present wherever `install` (below) runs its build, i.e. wherever `install_deps` or
  `install_dev_deps` ran. `devDependencies` covers everything build/lint/test-only (`typescript`,
  `@typescript/native-preview` (`tsgo`), `esbuild`, `esbuild-plugin-babel`,
  `babel-plugin-react-compiler`, `@babel/core`/`@babel/preset-typescript`/`@babel/preset-react`
  (the webview build's Babel pipeline — see "Build" above), `eslint` and its plugins, `vitest`,
  `jsdom`, `@testing-library/react`, ...).
* `lint` runs `eslint` (flat config in `eslint.config.mjs`) over `src/` and `test/`:
  `typescript-eslint`'s recommended rules; `eslint-plugin-import-x` (the actively-maintained,
  flat-config-native fork of the classic `eslint-plugin-import`) with its `recommended` and
  `typescript` presets, resolving this project's `shared/*`/`webview/*`/`host/*` aliases via
  `eslint-import-resolver-typescript` pointed at both tsconfigs; `eslint-plugin-react-hooks` and
  `eslint-plugin-react` (`recommended` + `jsx-runtime`, `react/prop-types` off since TypeScript
  already checks prop shapes) scoped to `src/webview/**/*.tsx`; `eslint-plugin-testing-library`'s
  `flat/react` preset scoped to `test/**` (`testing-library/no-manual-cleanup` is turned off —
  this project's `vitest.config.mts` doesn't set `test.globals: true`, so
  `@testing-library/react`'s own auto-cleanup, which relies on detecting a global `afterEach`,
  never engages; the explicit `afterEach(cleanup)` in `test/webview/App.test.tsx` is genuinely
  required, not redundant); and `eslint-plugin-prettier`/`eslint-config-prettier` so formatting
  violations surface as lint errors. `no-restricted-imports` blocks deep imports into a feature's
  internals from outside it (`webview/features/*/**`, `host/features/*/**`) — see `AGENTS.md`'s
  "vscode-plugin source tree" section.
* `test` runs `vitest run` over `test/` (see "Test tree" above for how it resolves the same
  rooted aliases as application code).
* `typecheck` runs `tsgo` against both tsconfigs (see "Build" above for why `tsgo`, not `tsc`).
* `compile` runs `typecheck`, then `copy:codicons` (see "Component library" above), then both
  bundles: `build:extension` (a direct `esbuild` CLI invocation) and `build:webview`
  (`build-webview.mjs`, esbuild's JS API plus the `esbuild-plugin-babel`/React Compiler pass —
  see "Build" above).
* `install` (not present in `klorb/Makefile`, since the Python side has no editor-installation
  step) runs `compile`, packages the result into a `.vsix` with `@vscode/vsce`, and installs
  it into the local VS Code with `code --install-extension` — the interop step needed to
  actually try the extension out, as opposed to just linting/testing it. This is the
  *development* build: unminified, `NODE_ENV=development` (so React's own dev-only warnings
  surface real bugs during testing), full sourcemaps.
* `dist` runs `compile:prod` — `build:extension:prod`'s `esbuild` CLI flags
  (`--minify --sourcemap=linked --sources-content=false --legal-comments=linked`) and
  `build:webview:prod`'s `node build-webview.mjs --prod` equivalents of the same options — and
  packages the result the same way `install` does, but doesn't also install it into the local VS
  Code — it
  produces the artifact meant for actual distribution (`vsce publish`, or handing the `.vsix` to
  someone else), not another local dev-loop iteration. See
  `docs/adrs/production-vsix-build-is-minified-and-drops-node-modules.md`.
* `.vscodeignore` excludes `node_modules/**`, `package-lock.json`, and `types/**` from every
  packaged `.vsix` (dev or prod) — since both the extension host and the webview are fully
  bundled by `esbuild`, nothing at runtime ever `require()`s a package out of `node_modules`, so
  shipping a second copy of every dependency alongside the bundle that already inlines them is
  pure waste. `**/*.map` is also excluded (sourcemaps stay in the local `out/` build output for
  debugging, not in the shipped package) — `**/*.LEGAL.txt` is deliberately *not* excluded, since
  it's the license attribution `--legal-comments=linked` collects for the bundled dependencies'
  code inside the very file it ships alongside.
* `clean` removes `out/`, `coverage/`, the packaged `.vsix`, and `tsconfig.tsbuildinfo`.
  `distclean` additionally removes `node_modules/`.

## Configuration

`package.json`'s `contributes.configuration` (title "Klorb") declares two settings, both read
(along with the OpenRouter API key — see below) by `extension.ts`'s `readServerOptions()` each
time a connection is started (at activation and on `klorb.restartServer`):

* `klorb.serverPath` (string, default `"klorb"`): the command run as `<serverPath> server` to
  launch the child process. Overriding it points the extension at a `klorb` not on `PATH` (e.g.
  a venv's `bin/klorb` during local development of the Python side).
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
   the streaming response/thinking block get an italic "(interrupted)" marker appended rather
   than just stopping.
4. Run **Klorb: New Session** from the command palette to clear the panel and start a fresh
   ACP session without restarting the child process; run **Klorb: Restart Server** to kill and
   respawn `klorb server` itself (e.g. after changing `klorb.serverPath`).
5. Ask for an edit to a file in the workspace. Watch its tool-call chip go from a busy spinner
   to a completed summary; click the chevron to expand it and see the colored diff (or the
   detail text, for a non-edit call); click "Open diff" to see the change in a real editor diff
   view; click a read call's title to jump to that file/line. Click "Expand all tool calls" in
   the history header to flip every chip at once, mirroring the TUI's Ctrl+O.
6. With a session's `permission_framework` at its `"ask"` default, run a prompt that uses `Bash`.
   The `ApprovalPanel` appears above the input; approve with "Allow for this session" and watch
   the command run and a compact "Decision: Allow for this session" record land in the history.
   Try Escape (denies once) and "Other…" (redirects with free text) on a later ask in the same
   session. Set `--max-tool-calls-per-turn 1` in a test config (see [[klorb-server]]'s
   `session-and-turns.md` reference) and trigger a second tool call in one turn to see the
   `_klorb/raiseToolCallLimit` native modal; "Continue" doubles the cap and lets the call proceed.
   Hide the auxiliary bar before triggering an ask to see the "Klorb needs your approval"
   notification, and click "Show Klorb" to bring the panel back into view.
7. Prompt the agent to "ask me three questions about X before proceeding" — one with several
   options, one plain free-text, one with options again. Answer the first by clicking an option,
   the second by typing into "Other…" and clicking Send, then press Escape on the third and
   observe the batch stop (no further question arrives) with a compact "(cancelled)" record in
   the history.
8. Click the status row's model chip (or run **Klorb: Select Model**) and pick a different
   model; send a prompt and confirm the reply used it. Click the thinking chip (or run
   **Klorb: Set Thinking**) and pick `High` from the `Off`/`Low`/`Medium`/`High` QuickPick;
   confirm the chip reads `High` and updates independently of the model chip. Click the
   thinking chip again and pick `Off`; confirm the chip reads `Off`. Click it once more and
   pick `Medium`, confirming a single pick both re-enables thinking and sets its effort in one
   step (no separate "enable" question, and no stale effort left over from before it was
   disabled). Click the permission badge (or run
   **Klorb: Cycle Permission Mode**, or press Shift+Tab with focus in the prompt textbox) to
   cycle to `[auto]` and watch a `Bash` prompt run without an `ApprovalPanel`; cycle to `[deny]`
   and watch the same kind of prompt fail closed. Send a prompt and confirm the status row's
   token tally shows both the `↑` context count and the `↓` output count once the turn
   completes. Run **Klorb: Show Session Stats** and confirm a `SessionStatsCard` lands in the
   history (aligned right-justified
   numbers across every section, plus a separated cost line — not a toast, not the output
   channel); run **Klorb: Reload Skills**
   and confirm the toast's skill count. Open a workspace VS Code itself already trusts but that
   Klorb hasn't seen before, and accept the "Trust this workspace in Klorb?" prompt — the panel's
   top title bar's `(Untrusted)` suffix should disappear.
9. Run **Klorb: Set OpenRouter API Key**, paste a key, then run **Klorb: Restart Server** and
   confirm the child still authenticates; run **Klorb: Clear OpenRouter API Key** and confirm a
   subsequent restart falls back to the inherited environment.
10. In a workspace with a chainlink task database (or ask the agent to create a few todos with
    `TodoCreate`), confirm the task panel appears docked above the history, collapsed to a
    one-line "Tasks: N open · ..." summary. Click it to expand the list; confirm the current task
    is starred, closing an issue (`TodoUpdate`) dims and strikes it through, and a blocked issue
    shows its "(blocked)" suffix — all live, without restarting the session. Click its header's
    pin icon to hide it, then open the status row's `^` menu and confirm its task-panel item now
    reads "Show Task Panel"; pick it and confirm the panel reappears (this is the only way back
    once the pin has hidden it) and the item now reads "Hide Task Panel" again. Run **Klorb:
    Toggle Task Panel** from the command palette as a third way to flip the same state; confirm
    the panel's shown/hidden state (not its expand/collapse state) survives hiding and re-showing
    the auxiliary bar.
11. Against a klorb server build that advertises `_klorb/enqueueMessage` (any current build
    does), start a slow prompt (e.g. one that calls `Bash` with a multi-second command under
    `[auto]` mode) and, while it's running, type a second message and press Enter. Confirm the
    input stays enabled (not disabled) and both Send and Stop are visible; confirm the new
    message renders in italic "Queued message" styling, then flips to regular prompt styling once
    the server confirms delivery. Confirm the prompt's own textarea regains keyboard focus once
    the turn ends, without clicking it. Kill the `klorb server` child process directly (e.g. from
    a terminal, `kill` its pid) while a turn is running; confirm the resulting history entry
    reads distinctly from an ordinary error and offers a "Restart Server" button, and that
    clicking it runs **Klorb: Restart Server**.

## Out of scope

* No persistence beyond `vscode.getState()`'s in-memory-while-the-window-is-open lifetime —
  nothing is written to disk, and history (including the status snapshot) is lost on
  `klorb.restartServer`, `klorb.newSession`, or a full window reload.
* No production (minified) webview bundle — see
  `docs/adrs/bundle-webview-script-with-esbuild-not-es-modules.md`'s reasoning, unchanged from
  the original stub.
