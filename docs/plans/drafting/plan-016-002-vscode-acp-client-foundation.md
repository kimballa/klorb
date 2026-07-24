# Plan 016, increment 002: VS Code ACP client foundation

> **Draft.** Part of [Plan 016](plan-016-acp-client-server.md). Do not implement from the
> `drafting/` folder.

## Goal

Rebuild the VS Code plugin's plumbing and UI foundation on ACP: the extension spawns
`klorb server` (from increment 001), connects over stdio with the official TypeScript SDK,
creates a session, and provides a streaming chat panel — prompt in, markdown response and
thinking text streaming out, with cancel. The webview moves to `@vscode-elements/elements`
components. This replaces the greet round-trip entirely.

## Dependencies

Add to `vscode-plugin/package.json` (then `make sync_deps` to refresh the lockfile):

* `dependencies`: `@agentclientprotocol/sdk` (^0.14) — extension-host runtime dep (the host
  `require()`s it at runtime, unlike the bundled webview deps; this is the plugin's first
  real `dependencies` entry, so note in the spec that `install_deps` now installs something).
* `devDependencies` (bundled into the webview or test-only): `@vscode-elements/elements`,
  `react-markdown`, `@testing-library/react`, `jsdom`.
* Vendor the vscode-elements TypeScript globals: create `vscode-plugin/types/global.d.ts`
  with the contents of
  `https://raw.githubusercontent.com/vscode-elements/examples/refs/heads/react-vite/react-vite/src/global.d.ts`
  (keep the upstream copyright/attribution comment if present; add the source URL in a
  comment). Add `"types"` dir to `tsconfig.webview.json`'s `include` so JSX recognizes
  `<vscode-button>` etc. React 19 renders custom elements natively — no wrapper package.

## Deliverables

### 1. Extension host: `AcpConnection` (`src/acpConnection.ts`)

Replaces the greet/JSONL logic currently in `klorbServerProcess.ts` (the spawn/kill/restart
`KlorbServerProcess` shell and its injectable `SpawnFn` survive; the `readline` + FIFO
promise queue goes away).

* Owns the SDK client-side connection bound to the child's stdout/stdin streams.
* `KlorbAcpClient` (`src/klorbAcpClient.ts`) implements the SDK's `Client` interface. This
  increment implements `sessionUpdate` (dispatching `agent_message_chunk` /
  `agent_thought_chunk` to a listener callback) and stubs the rest (`requestPermission`
  auto-answers with the first reject option and logs — increment 006 replaces this;
  fs/terminal methods return method-not-found-style failures since we won't advertise those
  client capabilities).
* Lifecycle: `start()` spawns the child (existing options: `serverPath`, `configPath`,
  `openRouterApiKey` env), performs `initialize` (assert protocol version compatibility;
  surface a readable error to the panel if the binary is an old pre-ACP klorb), then
  `newSession(cwd = first workspace folder, mcpServers: [])`, storing the `sessionId`.
  `prompt(text)` sends `session/prompt` and resolves with the stop reason. `cancel()` sends
  `session/cancel`. `stop()` kills the child and rejects any in-flight prompt with a
  restart-style error, mirroring today's `stop()` contract. `klorb.restartServer` and
  `klorb.restartSession` keep their meanings (restart child + re-initialize; `klorb.
  restartSession` now also calls `newSession` for a fresh conversation).

### 2. Typed webview message protocol (`src/shared/webviewMessages.ts`)

One discriminated-union module included by both tsconfigs (extension host + webview):

* Host → webview: `turnStarted`, `agentChunk {text}`, `thoughtChunk {text}`,
  `turnEnded {stopReason}`, `turnError {message}`, `sessionReset`.
* Webview → host: `submitPrompt {text}`, `cancelTurn`.
* Type-guard helpers for each direction (the existing `isSubmitMessage` pattern,
  generalized: one `parseHostMessage`/`parseWebviewMessage` narrowing function each, unit
  tested). All later increments extend this union rather than inventing ad hoc shapes.

`KlorbSessionViewProvider` routes: webview `submitPrompt` → `AcpConnection.prompt()`, ACP
updates → host messages. It also forwards `turnError` when a prompt rejects.

### 3. Webview UI rebuild (`src/webview/`)

* `App.tsx` becomes the layout shell described in the plan overview (history scroll +
  interaction area placeholder + input + status row placeholder; the task panel and status
  row arrive in later increments). Components under `src/webview/components/`:
  * `HistoryView` — ordered list of history entries. Entry model (in
    `src/webview/historyModel.ts`, a pure module): `{kind: 'prompt' | 'response' |
    'thinking' | 'error' | 'notice', text, streaming: boolean}`. Reducer-style pure
    functions apply host messages to the entry list (`applyHostMessage(entries, msg)`) so
    streaming append/rollover logic is unit-testable without React. Responses render via
    `react-markdown`; thinking renders as a collapsed-by-default disclosure
    (`<details>`-style, styled muted/italic like the TUI's thinking block) that streams
    while open.
  * `PromptInput` — `<vscode-textarea>`-based multi-line input reusing `classifyEnterKey`
    (Enter submits, Shift/Ctrl+Enter newline); disabled while a turn is in flight; a Stop
    button (`<vscode-button>`) appears during a turn and posts `cancelTurn` (Escape with
    input focused does the same).
* Keep: single `acquireVsCodeApi()` call threaded as a prop; `vscode.setState` persistence
  of entries; index keys on the append-only list; `#root { display: contents }`. Restyle
  `media/main.css` around vscode-elements components (drop the hand-rolled bubble look where
  a component now covers it; prompts stay visually distinct right-aligned blocks).
* esbuild continues to bundle everything (vscode-elements is Lit-based and bundles fine);
  no `<script type="module">` changes.

### 4. Docs

Rewrite `docs/specs/vscode-plugin.md` for the new architecture (ACP connection, message
protocol, component layout, build/test changes). ADR:
`docs/adrs/vscode-webview-stays-acp-ignorant-behind-typed-messages.md` (the host-translates
decision), and `docs/adrs/use-vscode-elements-for-webview-controls.md`.

## Tests (`vscode-plugin/test/`)

* `mockAgent.ts`: an SDK *agent-side* implementation connected to the code under test over
  Node `PassThrough` stream pairs — scriptable: enqueue session updates, respond to
  initialize/newSession/prompt with canned results, assert received calls. No child process.
* `acpConnection.test.ts`: initialize/newSession handshake, prompt resolves with stop
  reason, streamed updates reach the listener in order, cancel sends `session/cancel`,
  old-server (initialize failure) produces the readable error, `stop()` rejects in-flight
  prompts.
* `webviewMessages.test.ts`: parser/type-guard round-trips, unknown-shape rejection.
* `historyModel.test.ts`: streaming append (first chunk creates entry, later chunks extend),
  thinking vs response separation, `turnEnded` finalizes streaming flags, `turnError`
  appends error entry, `sessionReset` clears.
* `App.test.tsx` (jsdom + @testing-library/react): submit posts `submitPrompt` and echoes
  the prompt entry; incoming chunks render; input disabled during turn; Stop posts
  `cancelTurn`. Mock `acquireVsCodeApi` as today. If a vscode-elements component misbehaves
  under jsdom, assert against the custom-element tag boundary rather than its shadow
  internals (the logic under test lives in `historyModel` anyway).
* Keep `keyHandling.test.ts` and the surviving `klorbServerProcess` spawn tests.

## Checkpoint criteria

* `make -C vscode-plugin lint test compile` green; `make -C klorb lint typecheck test` still
  green (untouched).
* Manual: `make -C vscode-plugin install`, open the Klorb sidebar, submit a prompt against a
  real `klorb server`, watch thinking + response stream in, hit Stop mid-stream and see the
  cancelled turn. Include this recipe in the spec.
