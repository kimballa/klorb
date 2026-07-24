# Plan 016: ACP client / server — VS Code plugin with full TUI-equivalent functionality

> **Draft.** This plan describes a design for review, not a built feature. Nothing under
> `docs/plans/drafting/` should be treated as implemented, and nothing else should depend on it,
> until it's promoted to `ready/` and then archived.

## Summary

Klorb grows a real client/server split. The `klorb server` subcommand becomes a server
implementing the [Agent Client Protocol](https://agentclientprotocol.com) (ACP) — JSON-RPC 2.0
over newline-delimited stdio — replacing the current `greet`/`shutdown` JSONL stub
(`klorb.server.jsonl_server.JsonlServer`). The VS Code plugin becomes a full ACP client: it
spawns `klorb server` as a child process, drives it over ACP, and provides the complete
interactive experience the terminal TUI provides today — streaming responses and thinking,
live tool-call display, permission approvals, `AskUserQuestions`, task tracking, model and
thinking controls — rendered with `@vscode-elements/elements` web components in a React 19
webview, adapted to a tall-and-narrow docked sidebar.

This plan is the overview: architecture, protocol mapping, library choices, and testing
strategy. The work itself is broken into twelve self-contained increments,
`plan-016-001-*` through `plan-016-012-*`, each of which leaves both subprojects
lint/typecheck/test-clean and leaves the end-to-end system runnable.

## Why ACP

ACP is an open, versioned protocol purpose-built for exactly this shape: an editor ("Client")
driving an AI coding agent ("Agent") as a subprocess over stdio, with first-class protocol
support for nearly everything klorb's `TurnEventHandlers` event system already expresses:

| klorb concept (today) | ACP concept |
| --- | --- |
| `Session` / `send_turn()` | `session/new`, `session/prompt` (one prompt request = one turn) |
| `on_chunk` streamed response text | `session/update` → `agent_message_chunk` |
| `on_thinking_chunk` streamed reasoning | `session/update` → `agent_thought_chunk` |
| `on_tool_call_started` | `session/update` → `tool_call` (status `pending`/`in_progress`, kind, locations) |
| `on_tool_call` (finished) | `session/update` → `tool_call_update` (status `completed`/`failed`, content incl. diffs) |
| `on_permission_ask` → `PermissionDecision` | `session/request_permission` (options list; client answers with an `optionId`) |
| `cancel_event` / Escape abort | `session/cancel` notification; prompt resolves with `stopReason: "cancelled"` |
| Permission framework (`ask`/`auto`/`deny`, Shift+Tab cycle) | Session modes: `session/set_mode`, `current_mode_update` |
| Model choice, thinking enabled/effort | Session config options (`session/set_config_option`), with an ext-method fallback |
| Chainlink task tracking (`Todo*` tools, task sidebar) | `session/update` → `plan` (entries with `content`/`priority`/`status`), plus `_meta` for klorb-specific detail |
| Session naming (`on_session_name_changed`) | `session/update` → session-info/title update where the SDK supports it, else ext notification |
| `on_ask_user_questions` | **No standard fit** → `_klorb/askUserQuestions` extension request |
| `on_tool_call_limit_reached` | **No standard fit** → `_klorb/raiseToolCallLimit` extension request |
| `on_enqueue_message` / `user_interjections` | **No standard fit** → `_klorb/enqueueMessage` extension request + notifications |
| `EscalatePrivileges` | `session/request_permission` with klorb `_meta` detail |

The places marked "no standard fit" use ACP's sanctioned extensibility mechanisms (see
"Extensibility rules" below) rather than forking the protocol. Everything else is standard
ACP, which means klorb's server should, as a side effect, be drivable by *other* ACP clients
(Zed, neovim plugins, etc.) for the standard subset.

## Library choices

* **Python server: [`agent-client-protocol`](https://pypi.org/project/agent-client-protocol/)**
  (import name `acp`), the ACP org's Python SDK. It provides generated pydantic models for
  every protocol type (`acp.schema`), an `acp.Agent` async base class with methods matching
  the protocol (`initialize`, `new_session`, `prompt`, `set_session_mode`,
  `set_config_option`, `cancel`, `ext_method`, `ext_notification`, ...), a `Client` interface
  for the reverse direction (`session_update`, `request_permission`, ...), stdio JSON-RPC
  plumbing, and helper builders (`acp.helpers`: `text_block`, `start_tool_call`,
  `update_tool_call`, ...). Pydantic 2 models align with klorb's existing modeling stack.
  Pin `agent-client-protocol >= 0.7.0, < 0.8.0` (pre-1.0: pin the minor, and re-verify
  API-surface claims in each increment against the pinned version — this plan describes the
  0.7.x surface).
* **TypeScript client: [`@agentclientprotocol/sdk`](https://www.npmjs.com/package/@agentclientprotocol/sdk)**
  (~0.14.x), the official TS SDK (renamed from `@zed-industries/agent-client-protocol`).
  Provides both sides of the connection; we use its client side (`ClientSideConnection` or the
  pinned version's equivalent) over the child process's stdio, and its agent side in tests to
  build an in-process mock server.
* **Webview UI: React 19 + [`@vscode-elements/elements`](https://www.npmjs.com/package/@vscode-elements/elements)**
  for native VS Code styling. React 19 supports custom elements directly — no React wrapper
  package. TypeScript support for the custom elements comes from the global JSX declarations
  published in the vscode-elements examples repo
  (`https://raw.githubusercontent.com/vscode-elements/examples/refs/heads/react-vite/react-vite/src/global.d.ts`),
  vendored into a new `vscode-plugin/types/` directory.
* **Markdown rendering: `react-markdown`** in the webview, replacing nothing (the stub renders
  plain text). Chosen over `marked`+`innerHTML` because it renders to React elements without
  `dangerouslySetInnerHTML`, which matters in a CSP-locked webview fed model-generated text.

## Server architecture

### Naming: `AcpServer`, not `JsonlServer`

`JsonlServer` is replaced by **`AcpServer`** (`klorb/src/klorb/server/acp_server.py`). The name
follows the same convention `JsonlServer` used — named for the protocol it speaks — and beats
the alternatives: `RemoteServer` says nothing about the wire, `AgentServer` collides with ACP's
own "Agent" role terminology in a confusing way (in ACP, the *server process* implements the
"Agent" side of the protocol; an `AgentServer` class that *contains* an `Agent` implementation
reads redundantly). Supporting cast, all in `klorb/src/klorb/server/`:

* `acp_server.py` — `AcpServer`: owns the stream pair, constructs the SDK connection, runs the
  serve loop, owns process-lifecycle concerns (clean shutdown on EOF/disconnect).
* `klorb_agent.py` — `KlorbAcpAgent(acp.Agent)`: implements the protocol methods and owns the
  single live `Session`.
* `turn_bridge.py` — `TurnBridge`: the sync↔async bridge that runs `Session.send_turn()` on a
  worker thread and converts `TurnEventHandlers` callbacks into ACP traffic (see "Threading
  bridge" below).
* `update_mapping.py` — pure functions mapping klorb event payloads (`ToolCallEvent`,
  `PermissionAskContext`, chainlink issues, ...) to ACP schema objects. Kept free of I/O so
  they're trivially unit-testable.

### Streams, not stdin/stdout

`AcpServer` never touches `sys.stdin`/`sys.stdout` directly. A small `ServerStreams` class
(same module) holds the async read/write endpoints the connection is built from, with a
`ServerStreams.from_stdio()` factory as the only place real stdio is bound. `klorb.cli.
run_server_cli()` calls that factory; tests construct `ServerStreams` from in-memory duplex
pairs. A future websocket transport is a second factory on the same class — nothing else
changes. (The SDK's one-call `run_agent()` helper hard-wires stdio, so we use the SDK's
underlying connection class with injected streams instead; the factory is where the two meet.)

### Single top-level session

The server supports exactly **one live `Session` at a time**, matching klorb's current
singleton-session reality. `session/new` tears down any existing session (`Session.close()`)
and builds a fresh one — the same semantics as the TUI's `/clear`. The ACP `sessionId` echoed
back is `Session.id`. Requests naming a stale `sessionId` get a JSON-RPC error. `loadSession`
capability is not advertised (see "Future work"). Multiple top-level sessions and subagent
child sessions are explicitly future work; the ACP surface (sessionId on every message) is
already shaped for them, which is a large part of why ACP is the right protocol to adopt now.

### Threading bridge

`Session.send_turn()` is synchronous and blocking, and its `TurnEventHandlers` callbacks are
synchronous — some fire-and-forget (`on_chunk`), some blocking round-trips that need a user
answer (`on_permission_ask`). The ACP SDK is asyncio. `TurnBridge` reconciles the two, playing
the role `ReplApp`'s `call_from_thread` machinery plays in the TUI:

* `prompt()` runs `session.send_turn(...)` via `asyncio.to_thread()` (equivalently
  `loop.run_in_executor`), keeping the event loop free to service concurrent client requests
  (`session/cancel`, ext methods) mid-turn.
* Fire-and-forget events (`on_chunk`, `on_thinking_chunk`, `on_tool_call_started`,
  `on_tool_call`, ...) are enqueued (from the worker thread, via
  `loop.call_soon_threadsafe`) onto one ordered `asyncio.Queue` drained by a single sender
  task that awaits each `session/update` notification in order. One queue + one pump task is
  what guarantees updates hit the wire in the order the callbacks fired — independently
  scheduled coroutines would not.
* Blocking asks (`on_permission_ask`, `on_ask_user_questions`, `on_escalate_privileges`,
  `on_tool_call_limit_reached`) call
  `asyncio.run_coroutine_threadsafe(<client round-trip>, loop).result()` — the worker thread
  parks until the client answers, exactly as the TUI's worker parks on `call_from_thread`.
  The sender task drains any queued updates ahead of issuing the ask, so the ask never
  overtakes the events that led to it.
* `session/cancel` sets the turn's `cancel_event` (the same `threading.Event` mechanism
  Escape uses in the TUI); `send_turn` raises `ResponseAborted` and `prompt()` resolves with
  `stopReason: "cancelled"`.

### Extensibility rules (verbatim from the ACP spec, and how we apply them)

The ACP spec's extensibility page defines three mechanisms. The rules that bind us:

* Every protocol type has a `_meta: { [key: string]: unknown }` field for custom data.
  "Implementations **MUST NOT** add any custom fields at the root of a type that's part of
  the specification." All klorb custom data therefore rides in `_meta`, always under a single
  `"klorb"` key (e.g. `_meta: {"klorb": {"issueId": 12}}`) so it can't collide with other
  vendors.
* Any method name starting with `_` is reserved for extensions. Klorb extension methods are
  named `_klorb/<camelCaseName>` (e.g. `_klorb/askUserQuestions`). Unrecognized extension
  *requests* get the standard `-32601` Method-not-found error; unrecognized extension
  *notifications* are ignored.
* Extensions "**SHOULD** advertise their custom capabilities" during `initialize`. The server
  advertises `agentCapabilities._meta.klorb = {"askUserQuestions": true, "raiseToolCallLimit":
  true, "enqueueMessage": true, "sessionStats": true, "taskMeta": true, ...}` (grown per
  increment), and the client checks these flags before calling — so a stock ACP client that
  isn't klorb-aware still works for the standard subset, and the klorb client degrades
  gracefully against a newer/older server.

The canonical list of klorb extension methods, their params/result shapes, and their
capability flags lives (once implemented) in the rewritten `docs/specs/klorb-server.md`; each
increment that adds one specs it exactly.

## VS Code client architecture

The extension keeps its existing two-world split (extension host ↔ webview), and its existing
`Makefile`/esbuild/tsc build shape:

* **Extension host** (`src/`): `KlorbServerProcess` keeps its job (spawn/kill/restart the
  child, injectable `SpawnFn`) but its JSONL greet plumbing is replaced by
  `AcpConnection` — a thin owner of the SDK's client-side connection bound to the child's
  stdio, plus a `KlorbAcpClient` class implementing the SDK's `Client` interface
  (`sessionUpdate`, `requestPermission`, ext methods...). The extension host is where all
  editor-integration side effects happen: opening files at `tool_call` locations, showing
  diffs, VS Code commands, settings, workspace trust.
* **Webview** (`src/webview/`): React 19 app, all presentational state. The host↔webview
  message protocol grows from `{submit, reply}` to a typed union defined once in
  `src/shared/webviewMessages.ts` (shared by both tsconfigs) — host→webview messages mirror
  ACP session updates (already JSON-serializable), webview→host messages express user intent
  (`submitPrompt`, `cancelTurn`, `permissionDecision`, `openLocation`, `setModel`, ...). The
  webview never speaks ACP directly; the host translates. This keeps the ACP dependency out
  of the browser bundle and keeps the webview testable with plain message fixtures.
* **Layout for a tall-narrow sidebar** (top to bottom):
  1. **Task panel** — collapsible strip docked at the top listing chainlink-backed plan
     entries (current task starred); collapsed to a one-line summary ("3 open · 1 blocked ·
     task #12 in progress") by default.
  2. **History scroll** — prompts, streamed thinking (collapsed-by-default disclosure),
     streamed markdown responses, tool-call chips with expandable detail.
  3. **Interaction area** — permission asks / questions / escalation panels mount here,
     directly above the input, mirroring the TUI's non-modal interaction panel.
  4. **Prompt input** — multi-line, Enter submits / Shift+Enter newline (existing
     `classifyEnterKey` behavior), disabled while a turn is in flight, with a Stop control.
  5. **Status row** — model + thinking effort (click → picker), permission-mode badge
     (click → cycle, mirroring Shift+Tab), token tally, session title.

### TUI features deliberately *not* recapitulated

VS Code already provides these, so the plugin integrates instead of reimplementing:

* **Diff/read previews and expansion overlays** → tool-call chips link to real editor tabs
  (`vscode.diff` for edits, `vscode.window.showTextDocument` at a line for reads).
* **Theme commands** → the webview inherits VS Code theming via `--vscode-*` CSS variables and
  vscode-elements.
* **Keybinding config** → VS Code keybindings on contributed commands.
* **`!` shell commands** → the integrated terminal.
* **Workspace trust bootstrap prompt** → VS Code's own workspace-trust state, bridged to the
  server (increment 008/009).
* **Input history recall / Ctrl+R** → deferred (see Future work); VS Code users retain
  ordinary editor conveniences meanwhile.

Commands that affect how the *agent session* operates (model, thinking effort, permission
framework, clear session, session stats, reload skills, trust workspace) **are** all
represented in the client. Pure-TUI conveniences (theme, keybinds) are not.

## Testing strategy

Both subprojects are tested independently via their own `make test`; neither test suite ever
spawns the other side for real.

* **Python** (`klorb/tests/klorb/server/`): a test harness (`acp_harness.py`) wires an
  `AcpServer` to the SDK's *client-side* connection over in-memory duplex streams inside one
  event loop — real protocol traffic, no subprocess. The `Session` under the server is real,
  but its `ApiProvider` is the same scripted mock pattern `tests/klorb/session/test_session.py`
  already uses (a `Mock` whose `send_prompt` side effect streams scripted chunks/tool calls),
  and chainlink is faked at the `ChainlinkClient` seam. We are proving the server's protocol
  behavior — that klorb events become correct ACP traffic and ACP requests drive the session
  correctly — not re-proving `Session` internals, which have their own suite. Pure mapping
  functions in `update_mapping.py` additionally get direct unit tests.
* **TypeScript** (`vscode-plugin/test/`): extension-host logic is tested against a
  `MockAgent` — the SDK's *agent-side* connection running in-process over Node `PassThrough`
  stream pairs — with scripted session updates and canned responses; `KlorbServerProcess`
  keeps its fake-`SpawnFn` tests. Webview components are tested with vitest + jsdom +
  `@testing-library/react`, driven purely by the typed host↔webview message fixtures (no ACP,
  no VS Code API — `vscode` and `acquireVsCodeApi` are injected/faked as today). Non-trivial
  view logic stays in pure functions (the `classifyEnterKey` precedent) so most assertions
  don't need DOM at all.
* **Manual end-to-end**: each increment's doc ends with a "try it" recipe (run `klorb server`
  by hand and paste ACP JSON-RPC lines, or `make install` the extension and drive it).

## Increments

Order matters: each is a runnable checkpoint, and the client increments each consume the
server increment landed just before them. The old JSONL stub protocol was a proof of concept
and is erased outright in 001, with no compatibility shim; the stub plugin's greet round-trip
is replaced in 002 — between those two checkpoints the plugin still builds and runs, but its
send button surfaces a protocol error in the panel until 002 lands.

| Increment | Side | Delivers |
| --- | --- | --- |
| [plan-016-001-python-acp-server-core](plan-016-001-python-acp-server-core.md) | server | `AcpServer` replaces `JsonlServer`: initialize / new-session / prompt with streamed response+thinking, cancel; stream-pair abstraction; CLI rewire |
| [plan-016-002-vscode-acp-client-foundation](plan-016-002-vscode-acp-client-foundation.md) | client | ACP SDK + vscode-elements + React 19 foundation; streaming chat replaces greet; mock-agent test harness |
| [plan-016-003-python-tool-call-updates](plan-016-003-python-tool-call-updates.md) | server | `tool_call`/`tool_call_update` session updates: kinds, locations, diff content, failure states |
| [plan-016-004-vscode-tool-call-rendering](plan-016-004-vscode-tool-call-rendering.md) | client | Tool-call chips with running animation, expandable detail, open-file / open-diff editor integration |
| [plan-016-005-python-permission-asks](plan-016-005-python-permission-asks.md) | server | `session/request_permission` with the allow/deny × scope option grid, bash multi-item asks, risk-classifier grants, `EscalatePrivileges`, `_klorb/raiseToolCallLimit` |
| [plan-016-006-vscode-approval-panels](plan-016-006-vscode-approval-panels.md) | client | Approval panel above the input: option grid, command preview + expand, free-text "other", escalation + limit prompts |
| [plan-016-007-ask-user-questions-end-to-end](plan-016-007-ask-user-questions-end-to-end.md) | both | `_klorb/askUserQuestions` ext method and the questions panel |
| [plan-016-008-python-session-controls](plan-016-008-python-session-controls.md) | server | Session modes (permission framework), model/thinking config options, session naming + token-usage updates, `_klorb/sessionStats`, workspace trust, skills reload |
| [plan-016-009-vscode-session-controls](plan-016-009-vscode-session-controls.md) | client | Status row, model/thinking pickers, permission-mode badge, new-session command, stats display, trust bridging |
| [plan-016-010-python-task-plan-updates](plan-016-010-python-task-plan-updates.md) | server | Chainlink → ACP `plan` session updates with `_meta.klorb` issue detail |
| [plan-016-011-vscode-task-panel](plan-016-011-vscode-task-panel.md) | client | Collapsible top-docked task panel rendering plan updates |
| [plan-016-012-queued-messages-and-interrupt-polish](plan-016-012-queued-messages-and-interrupt-polish.md) | both | `_klorb/enqueueMessage` mid-turn queueing, queued-message rendering, abort/interrupt polish, error surfaces |

Every increment also: updates the relevant spec(s) (`docs/specs/klorb-server.md`,
`docs/specs/vscode-plugin.md` — current-state documents, rewritten as the feature lands, per
AGENTS.md's spec rules), records genuinely contested decisions as ADRs, adds new dependencies
through the sanctioned flows (`.claude/skills/add-python-dependency` for Python; `npm install`
with a committed lockfile for the plugin), and leaves `make lint typecheck test` green in
each touched subproject.

## How to execute an increment (instructions for the implementing agent)

You have been asked to implement one increment of Plan 016 — say `plan-016-00N-<slug>`. This
section is your operating procedure. Follow it step by step.

### Scope discipline

1. Read `docs/plans/README-PLANS.md` first and obey it (plans only execute from `ready/`,
   with explicit permission).
2. Read **this overview in full**, then your increment's own document in full, before
   writing any code. The overview owns the architecture and vocabulary (`AcpServer`,
   `TurnBridge`, `ServerStreams`, the `_klorb/*` namespace, the ordered-outbound-queue rule);
   your increment doc owns the deliverables. If they seem to conflict, the increment doc
   wins for scope and this overview wins for architecture — and say so in your report.
3. Implement **only your increment**. Do not start the next one, and do not "improve" a
   later increment's territory while you're nearby. If you notice a real problem in a later
   increment's plan, note it in your report; don't act on it.
4. Increments 002+ assume every lower-numbered increment is already merged. Do not re-read
   the archived plan docs of completed increments to learn current behavior — read the
   *specs* (`docs/specs/klorb-server.md`, `docs/specs/vscode-plugin.md`) and the code, which
   are kept current as each increment lands. Plan docs describe intent at planning time and
   are not maintained.

### Before coding

1. Read the specs and source files your increment doc names. For server increments that
   means at minimum `docs/specs/klorb-server.md`, `docs/specs/session-and-turns.md`, and
   `klorb/src/klorb/session/events.py`; for client increments,
   `docs/specs/vscode-plugin.md` and the existing `vscode-plugin/src/` layout.
2. **Verify the SDK surface.** This plan was written against `agent-client-protocol` 0.7.x
   (Python) and `@agentclientprotocol/sdk` 0.14.x (TypeScript). Where your increment names
   an SDK class/method/field, confirm it exists in the installed, pinned version before
   building on it (read the package's own source in the venv / `node_modules`). If the real
   surface differs, adapt to the SDK, keep this plan's *wire-level* contracts (method names,
   `_meta.klorb` shapes, capability flags — those are klorb's own and not SDK-dependent),
   and record the deviation in the spec you update.
3. Do the architecture review README-PLANS.md prescribes: check the increment's approach
   still fits the codebase as it exists today, and raise concerns before implementing.

### While coding

1. Follow AGENTS.md and CLAUDE.md without exception: copyright headers on new files
   (`# © Copyright 2026 Aaron Kimball` / `// © Copyright 2026 Aaron Kimball`), explicit
   typing everywhere, state encapsulated in classes (no module-level mutable globals, no
   loose tuples), constants defined once and imported, `logger.debug` breadcrumbs on
   consequential actions, comments only for non-obvious WHY.
2. New wire surface goes through the established registries: an extension method must be
   named `_klorb/<camelCase>`, advertised under the appropriate side's
   `capabilities._meta.klorb`, gated on the peer's advertisement before use, and added to
   the extension-method registry section of `docs/specs/klorb-server.md` with full
   param/result shapes. Custom data rides only in `_meta.klorb` — never as new fields on
   standard ACP types.
3. Write the tests your increment doc lists. They are a floor, not a ceiling; they are
    also a scope guide — don't re-test `Session` internals or other already-covered
    machinery beyond what the listed assertions need.

### Before declaring done

 1. Run, and get green: `make -C klorb lint typecheck test` and/or
    `make -C vscode-plugin lint test compile` (whichever sides you touched), **and**
    `make lint_docs` at the repository root (CI enforces it on every markdown change,
    including the spec updates you just made).
 2. Update the spec(s) to describe the *new current state* (present tense, no
    diff-narration); write any ADR your increment doc calls for; log any deliberately
    deferred follow-ups in `TODO.md` under a `### Plan 016` section.
 3. Walk the increment's "Checkpoint criteria" manually — including the hand-run recipe —
    and report the outcome honestly, including anything that didn't work.
 4. When the increment is complete and merged, `git mv` its plan document to
    `docs/plans/archive/`. The overview document stays in place until the final increment
    (012) archives it too.

## Decisions taken (flagging for review)

1. **`AcpServer`** as the replacement name for `JsonlServer` (rationale above).
2. **Official SDKs on both sides** rather than hand-rolled JSON-RPC: `agent-client-protocol`
   (Python) and `@agentclientprotocol/sdk` (TypeScript).
3. **Permission framework ⇄ ACP session modes** (`ask`/`auto`/`deny` as three modes). This is
   how other ACP agents expose approval posture, so stock clients get it for free.
4. **Model / thinking ⇄ ACP session config options** (select + boolean), with `_klorb/*` ext
   fallback only if the pinned SDK version's config-option support proves too immature — the
   001 implementer verifies and records the outcome in the spec.
5. **AskUserQuestions via `_klorb/askUserQuestions`**, not ACP elicitation: the tool's
   multi-question, header/options/other-text structure doesn't map onto the current
   elicitation shape without loss, and elicitation is one of the newest, least-settled corners
   of the protocol. Revisit when elicitation stabilizes.
6. **Risk classification stays server-side.** The TUI runs `classify_command_risk` client-side
   today; in the split world the server runs it (it owns the OpenRouter key and the
   `sibling_items` batching) and ships display-ready grant previews to the client in `_meta`.
7. **Client-side queueing is not enough** for mid-turn user messages — klorb's
   `user_interjections` deliver *into the running turn*, which is a real capability — hence
   the `_klorb/enqueueMessage` ext method rather than having the client hold messages until
   the turn ends.
8. **Webview stays ACP-ignorant** — the extension host translates ACP to a typed
   webview-message protocol. Keeps the browser bundle lean and the UI testable with fixtures.
9. **Support for ACP clients other than the official plugin is best-effort**. 
   We don't have an active test plan for other ACP clients (e.g. Zed). "Should mostly work,
   but left untested" is the current strategy.

Resolved during drafting review: the `_klorb/askUserQuestions` extension method (decision 5)
is confirmed over ACP elicitation; the old JSONL stub protocol is erased outright in 001 with
no compatibility shim; and the OpenRouter API key moves to VS Code `SecretStorage` as part of
increment 009.

## Future work

* Websocket (or other socket) transport: a second `ServerStreams` factory + client-side
  connect option; the protocol layer is transport-agnostic by construction.
* Multiple concurrent top-level sessions per server, and subagent child sessions
  (`Session.root_id` already anticipates the latter; ACP sessionIds already namespace it).
* `session/load` / `listSessions`: mapping `last-session.json` restore (and eventually a real
  session store) into ACP's load/list surface, so the plugin can resume conversations.
* Editor-buffer filesystem: ACP clients can serve `fs/read_text_file`/`fs/write_text_file` so
  the agent sees unsaved editor state; klorb's file tools could route through the client when
  connected. Significant permission-model interaction; deliberately deferred.
* ACP terminal support (`terminal/*` methods) as an alternative surface for `BashTool` output.
* Prompt-input history recall (and reverse-i-search) in the webview, backed by the same
  per-project history file the TUI appends to.
* Production (minified) webview bundle once the extension ships beyond local dev.
