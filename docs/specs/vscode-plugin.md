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
button (or Escape while a turn is in flight) sends `session/cancel`.
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
  (see [[klorb-server]]'s extension-methods section), and then `newSession(cwd)`, storing the
  returned `sessionId` and forwarding the response's `modes`/`_meta.klorb.workspace`/`.title`
  to the listener as a `SessionInfo` via `onSessionInfo()` (`sessionInfoFromResponse()`, see
  "Status row and session controls" below). `prompt(text)` sends `session/prompt` with one
  `TextContentBlock` and resolves with the ACP `stopReason`; only one prompt may be in flight at
  a time (matching [[klorb-server]]'s own one-prompt-at-a-time rule), so a second call while one
  is running rejects immediately rather than queuing. `cancel()` sends `session/cancel` as a
  fire-and-forget notification — the in-flight `prompt()` call still resolves normally once the
  server winds the turn down and replies with `stopReason: "cancelled"`. `setSessionMode(modeId)`
  sends `session/set_mode`; `extMethod(method, params)` calls a `_klorb/*` extension method
  against the live session, injecting `sessionId` into `params` automatically -- both are the
  low-level wire calls `SessionControls` builds its typed control-plane surface on top of.
  `stop()` kills the child and rejects any in-flight `prompt()` with a restart-style error; the
  same rejection fires automatically if the connection closes out from under an in-flight
  prompt (child crash, unexpected EOF). `errorMessage()` (exported alongside the class) renders
  both real `Error` instances and the SDK's plain `{code, message}` JSON-RPC rejection objects
  as a readable string, since ACP request failures reject with the latter shape, not an
  `Error`. The `client` getter exposes the live `KlorbAcpClient` (`undefined` before `start()`
  completes or after `stop()`), which `KlorbSessionViewProvider` uses to forward a
  `permissionDecision` webview message and to trigger `repostPendingAsk()` after the webview
  view is recreated (see "Approval panel" below).
* `vscode-plugin/src/host/features/acp/klorbAcpClient.ts`'s `KlorbAcpClient` implements the ACP
  SDK's `Client` interface: the handler for requests/notifications the server sends back over
  the connection.
  `sessionUpdate()` dispatches `agent_message_chunk`/`agent_thought_chunk` text content to a
  `SessionUpdateListener` (`onAgentText`/`onThoughtText`), flattens `tool_call`/`tool_call_update`
  updates into `ToolCallStartedMessage`/`ToolCallUpdatedMessage` (`onToolCallStarted`/
  `onToolCallUpdated` — see "Tool-call rendering and editor integration" below), dispatches
  `current_mode_update`/`session_info_update` to `onModeChanged`/`onSessionTitleChanged` (the
  latter only when the update carries a `title` field at all), and logs (rather than errors on)
  any other update kind, since later increments add handling for `plan` etc. `extNotification()`
  dispatches `_klorb/usage` to `onUsageUpdate(usedTokens, maxTokens)` (logging and ignoring a
  malformed payload) and logs-and-ignores any other extension notification, per ACP's own
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
  (see "Webview message protocol" below). `onAgentText`/`onThoughtText` (the
  `SessionUpdateListener` methods `AcpConnection` calls as chunks stream in) post `agentChunk`/
  `thoughtChunk` host messages; `onToolCallStarted`/`onToolCallUpdated` post the flattened
  `toolCallStarted`/`toolCallUpdated` messages as-is, and `onToolCallUpdated` additionally
  records any `diff` payload with the shared `EditorIntegration` (see "Tool-call rendering and
  editor integration" below) before posting, since the diff text isn't retained anywhere else
  once flattened into the webview message. `onSessionInfo`/`onModeChanged`/
  `onSessionTitleChanged`/`onUsageUpdate` (see "Status row and session controls" below) each
  delegate straight to the matching `SessionControls.apply*()` method, set via
  `setSessionControls()` the same way `setConnection()` wires the connection. `_runTurn(text)`
  — invoked for a `submitPrompt` message — posts `turnStarted`, awaits `AcpConnection.prompt
  (text)`, and posts either `turnEnded {stopReason}` or (on rejection) `turnError {message}`; a
  `cancelTurn` message calls `AcpConnection.cancel()` directly, with no reply of its own (the
  in-flight prompt's own `turnEnded`/`turnError` follow-up is the confirmation);
  `openLocation`/`openDiff` messages are routed to `EditorIntegration.openLocation()`/
  `openDiff()`; a `permissionDecision` message is routed to `AcpConnection.client?.
  resolvePermissionDecision()` (see "Approval panel" below); `pickModel`/`cyclePermissionMode`
  messages execute the `klorb.selectModel`/`klorb.cyclePermissionMode` commands, so a status
  row click drives the same code path as its command-palette equivalent.
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
  overflows the panel).

### Webview UI structure

* `vscode-plugin/src/webview/main.tsx` is the webview's entry point, bundled separately (see
  "Build" below) and loaded as a plain classic `<script>`. It imports the
  `@vscode-elements/elements` custom-element modules used by the panel (registering
  `<vscode-textarea>`/`<vscode-button>` with the browser), calls `acquireVsCodeApi()` exactly
  once, reads any persisted `SessionState` via `vscode.getState()`, and mounts `<App vscode=
  {vscode} initialEntries={state.entries} />` into `#root` with `react-dom/client`'s
  `createRoot()`. Calling `acquireVsCodeApi()` a second time anywhere throws and silently
  aborts whatever called it — the VS Code webview API only allows one call per page load —
  which is why the single `vscode` value from that one call is threaded through as a prop
  rather than re-acquired (see `docs/adrs/call-acquirevscodeapi-exactly-once-per-webview-page.md`).
* `vscode-plugin/src/webview/App.tsx`'s `App` component is the panel's layout shell, top to
  bottom: the title (`.title`, `sessionTitleText()` — the active session's `sessionTitle`, or
  `New session…` until one arrives, with an `(Untrusted)` suffix appended whenever
  `workspaceTrusted === false`, TUI header parity), `HistoryView`, an `#interaction-area` div
  that mounts `ApprovalPanel` while a permission ask is outstanding or `QuestionPanel` while an
  `AskUserQuestions` question is outstanding, `PromptInput`, and `StatusRow` (see "Status row
  and session controls" below). It owns all interactive state: `entries` (a `HistoryEntry[]`,
  seeded from `initialEntries`),
  `inFlight` (whether a turn is currently running), `expandAllToolCalls` (the global "expand all
  tool calls" toggle — see `features/history` below), `pendingInteraction` (a `PermissionAskMessage
  | QuestionAskMessage | undefined`, seeded from `initialPendingInteraction` — see "Approval and
  question panels" below), and `status` (a `StatusSnapshot`, seeded from `initialStatus` — see
  "Status row and session controls" below). A `window` `message` listener (subscribed for the
  panel's lifetime via a `useEffect` keyed on `expandAllToolCalls`, so a newly-started tool call
  always sees the current toggle state) parses each incoming payload with `parseHostMessage()`
  and applies it to `entries`/`inFlight`/`pendingInteraction` via the pure functions in the
  `features/history` feature, and replaces `status` wholesale with a `statusUpdate` message's
  own fields (never merged — the host always posts the complete currently-known snapshot, see
  "Status row and session controls" below). Submitting a prompt appends a `'prompt'` entry
  optimistically, sets `inFlight` to `true` (the host's own `turnStarted`/`turnError` follow-up
  confirms or corrects it), and posts `{type: 'submitPrompt', text}`. Toggling the global
  tool-call expand mode flips `expandAllToolCalls` and applies it to every existing tool-call
  entry at once (`applyExpandAllToolCalls`); toggling one chip's own chevron flips just that
  entry (`applyToolCallExpandedToggle`) — both handlers are passed down to `HistoryView`.
  `handleApprovalDecision()` (passed to `ApprovalPanel` as `onDecision`) appends an
  `appendInteraction()` record, clears `pendingInteraction`, and posts `{type:
  'permissionDecision', ...}` back to the host; `handleQuestionAnswer()` (passed to
  `QuestionPanel` as `onAnswer`) is the parallel handler, appending an `appendQuestionInteraction()`
  record and posting `{type: 'questionAnswer', ...}`; `pickModel()`/`cyclePermissionMode()`
  (passed to `StatusRow` as `onPickModel`/`onCyclePermissionMode`) post `{type: 'pickModel'}`/
  `{type: 'cyclePermissionMode'}`. A separate `useEffect` keyed on
  `entries`/`pendingInteraction`/`status` calls `vscode.setState({entries, pendingInteraction,
  status})` (so history, an unanswered interaction, and the status snapshot all survive
  `retainContextWhenHidden`'s context teardown/rebuild) and scrolls the history's last child
  into view. `App`'s returned tree is wrapped in `<VsCodeApiProvider vscode={vscode}>` (see
  below) so any descendant can reach `vscode` via `useVsCodeApi()` without it being threaded
  through as an explicit prop down every intermediate component.
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
  * `applyHostMessage(entries, message, expandAllToolCalls = false)` is the `HostMessage`
    reducer: `agentChunk`/`thoughtChunk` extend the trailing streaming entry of the matching
    kind, or start a new one if the last entry is a different kind (or not currently streaming)
    — so thinking and response phases interleave correctly across a turn. `turnEnded` finalizes
    every streaming entry's `streaming` flag and, for any `stopReason` other than `"end_turn"`,
    appends a `'notice'` entry naming the reason. `turnError` finalizes streaming entries and
    appends an `'error'` entry. `sessionReset` clears the list. `toolCallStarted` appends a new
    `'toolCall'` entry with `status: 'in_progress'` and `expanded` seeded from
    `expandAllToolCalls`. `toolCallUpdated` mutates the matching `callId`'s entry in place
    (status/title/content/diff/locations), or appends a new entry (also seeded from
    `expandAllToolCalls`) if no `toolCallStarted` for that `callId` was ever seen — the fallback
    for a call that failed before `on_tool_call_started` could fire (e.g. malformed arguments).
  * `applyTurnFlag(inFlight, message)` is the parallel reducer for the `inFlight` boolean:
    `turnStarted` raises it, `turnEnded`/`turnError`/`sessionReset` clear it, every other
    message leaves it unchanged.
  * `applyExpandAllToolCalls(entries, expand)` sets every `'toolCall'` entry's `expanded` flag to
    `expand` at once — the reducer behind the global toggle, mirroring the TUI's Ctrl+O
    (`klorb.tui.mixins.key_actions.action_toggle_tool_call_detail`), which flips every
    `ToolCallStatic` in the history together. `applyToolCallExpandedToggle(entries, callId)`
    flips just the one named entry (a chip's own chevron), independently of the global mode.
  * `applyPendingInteraction(pendingInteraction, message)` is the parallel reducer for `App`'s
    `pendingInteraction` state: a `permissionAsk` or `questionAsk` message replaces it (the
    server never sends a concurrent second one, of either kind), `sessionReset` clears it, every
    other message leaves it unchanged. A resolved decision/answer clears `pendingInteraction`
    through `App`'s own `handleApprovalDecision()`/`handleQuestionAnswer()`, not through this
    reducer.
  * `HistoryView` renders a small fixed header (the global "expand all tool calls"
    `<vscode-button>`) above the scrolling `HistoryEntry[]` list: `'prompt'` entries as a
    right-aligned `.bubble` (index-keyed — safe here specifically because entries only ever
    append, never reorder or get removed or inserted in the middle, the one case React's own
    docs call out as fine for index keys); `'response'` entries through `react-markdown`;
    `'thinking'` entries as a collapsed-by-default `<details>` disclosure (muted/italic styling,
    matching the TUI's thinking block) that keeps streaming into its body while the reader has
    it open; `'error'`/`'notice'`/`'interaction'` entries as plain styled text; `'toolCall'`
    entries as a `ToolCallChip`; `'sessionStats'` entries as a `SessionStatsCard` (see "Status
    row and session controls" below).
* `vscode-plugin/src/webview/components/PromptInput.tsx` renders the `<vscode-textarea>` +
  `<vscode-button>` input row: disabled (and the button replaced by a Stop button) while
  `inFlight` is true, and additionally styled with the `.input-row-muted` CSS class (dimmed
  opacity) while its `muted` prop is set — `App` passes `muted={pendingInteraction !==
  undefined}`, since a permission ask or question ask always arrives mid-turn (`inFlight` is
  already true), so `muted` layers the TUI's interaction-mode visual treatment on top of the
  already-disabled input rather than changing whether it's disabled. Its own `onKeyDown` handles
  Shift+Tab (calls `onCyclePermissionMode`, `preventDefault`ed so it doesn't fall through to the
  browser's default tab-order navigation — mirroring the TUI's own Shift+Tab), Escape (calls
  `onCancel` when `inFlight`), and Enter, delegating the submit-vs-newline decision to
  `keyHandling.ts`'s `classifyEnterKey()`.
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
  own bullet above). The model chip and the thinking chip are separate, independently clickable
  buttons, each opening its own picker: the model chip reads `model`, or `...` before the first
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
  `PickModelMessage`/`PickThinkingMessage`/`CyclePermissionModeMessage` (webview → host)
  are the three bare `{type: 'pickModel'}`/`{type: 'pickThinking'}`/
  `{type: 'cyclePermissionMode'}` intents the status row's clickable chips (and the prompt
  input's Shift+Tab handler, for the last one) post. `KlorbSessionViewProvider._handleMessage()`
  maps `pickModel`/`pickThinking`/`cyclePermissionMode` to the same
  `klorb.selectModel`/`klorb.setThinking`/`klorb.cyclePermissionMode` commands the command
  palette itself runs, rather than duplicating their `QuickPick` flows.
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
  * All eight are contributed in `package.json` with "Klorb: …" titles.
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
  activation regardless of how many times `offerIfNeeded()` is called.
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

### Webview message protocol

The webview and the extension host exchange messages shaped by the discriminated unions in
`vscode-plugin/src/shared/webviewMessages.ts` — one module included by both tsconfigs (host
and webview) so the same types check both sides — over the standard `vscode.postMessage()` /
`window.addEventListener('message', ...)` webview messaging channel. The webview never speaks
ACP directly; `KlorbSessionViewProvider` is the only place that translates between the two (see
`docs/adrs/vscode-webview-stays-acp-ignorant-behind-typed-messages.md`).

* Webview → host (`WebviewMessage`): `{type: 'submitPrompt', text: string}` (once per submitted
  prompt), `{type: 'cancelTurn'}` (Stop button or Escape while a turn is running),
  `{type: 'openLocation', path: string, line?: number}` (a tool-call title link),
  `{type: 'openDiff', callId: string, path: string}` ("Open diff"), `PermissionDecisionMessage`
  ("Approval and question panels" above) answering a `permissionAsk`, `QuestionAnswerMessage`
  ("Approval and question panels" above) answering a `questionAsk`, `{type: 'pickModel'}` and
  `{type: 'cyclePermissionMode'}` ("Status row and session controls" above, the status row's
  two clickable chips).
* Host → webview (`HostMessage`): `{type: 'turnStarted'}`, `{type: 'agentChunk', text:
  string}`, `{type: 'thoughtChunk', text: string}`, `{type: 'turnEnded', stopReason: string}`,
  `{type: 'turnError', message: string}`, `{type: 'sessionReset'}`,
  `{type: 'toolCallStarted', callId, title, kind, locations}`, and
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
  `QuestionAskMessage` ("Approval and question panels" above), `StatusUpdateMessage`, and
  `SessionStatsMessage` (both "Status row and session controls" above).
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
  `esbuild src/webview/main.tsx --bundle --format=iife --platform=browser` (the `build:webview`
  npm script) bundles `src/webview/main.tsx` into one self-contained `out/webview/main.js` with
  React, `react-dom/client`, `react-markdown`, and `@vscode-elements/elements` all inlined
  alongside the plugin's own webview code. The `--define:process.env.NODE_ENV=\"development\"`
  flag and the choice to skip `--minify` are unchanged from the original stub (see
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

Markdown responses render via `react-markdown`, chosen over `marked` + `innerHTML` because it
renders to React elements without `dangerouslySetInnerHTML` — relevant since the rendered text
is model-generated and the webview runs under a CSP-locked `vscode-webview://` origin.

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
  at runtime, unlike the bundled webview deps — the plugin's first true runtime dependency),
  `@vscode-elements/elements`, `react`, `react-dom`, and `react-markdown` — the last four are
  runtime dependencies of the *webview*, not the host, but `esbuild` inlines them into
  `out/webview/main.js` at build time rather than the packaged extension `require()`-ing a
  separate `node_modules` copy at runtime, so from `vsce`/`npm ci`'s point of view they still
  need to be present wherever `install` (below) runs its build, i.e. wherever `install_deps` or
  `install_dev_deps` ran. `devDependencies` covers everything build/lint/test-only (`typescript`,
  `@typescript/native-preview` (`tsgo`), `esbuild`, `eslint` and its plugins, `vitest`, `jsdom`,
  `@testing-library/react`, ...).
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
* `compile` runs `typecheck` then both `esbuild` bundles (`build:extension`, `build:webview`).
* `install` (not present in `klorb/Makefile`, since the Python side has no editor-installation
  step) runs `compile`, packages the result into a `.vsix` with `@vscode/vsce`, and installs
  it into the local VS Code with `code --install-extension` — the interop step needed to
  actually try the extension out, as opposed to just linting/testing it. This is the
  *development* build: unminified, `NODE_ENV=development` (so React's own dev-only warnings
  surface real bugs during testing), full sourcemaps.
* `dist` runs `compile:prod` (the `:prod` `esbuild` scripts — `--minify`,
  `--define:process.env.NODE_ENV=\"production\"` for the webview bundle,
  `--sourcemap=linked --sources-content=false`, `--legal-comments=linked`) and packages the
  result the same way `install` does, but doesn't also install it into the local VS Code — it
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
   the turn end with a "Turn ended: cancelled" notice.
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

## Out of scope

* No task panel yet — chainlink-backed plan updates land in `plan-016-011`.
* No mid-turn message queueing — a second `submitPrompt` while a turn is in flight is rejected
  by `AcpConnection.prompt()` (mirroring [[klorb-server]]'s own one-prompt-at-a-time rule), not
  queued client-side. Lands in `plan-016-012`.
* No persistence beyond `vscode.getState()`'s in-memory-while-the-window-is-open lifetime —
  nothing is written to disk, and history (including the status snapshot) is lost on
  `klorb.restartServer`, `klorb.newSession`, or a full window reload.
* No production (minified) webview bundle — see
  `docs/adrs/bundle-webview-script-with-esbuild-not-es-modules.md`'s reasoning, unchanged from
  the original stub.
