# `klorb server`

## Summary

`klorb server` runs klorb as a persistent, non-interactive process that speaks the [Agent
Client Protocol](https://agentclientprotocol.com) (ACP) — JSON-RPC 2.0 over newline-delimited
stdio — for driving klorb from another program (an IDE extension, a supervisor process, a test
harness) rather than a terminal. It's reachable as a CLI subcommand (`klorb server --config
PATH`), where `--config` is the only flag of its own today. See
[the ACP-not-bespoke-JSONL ADR](../adrs/speak-acp-not-bespoke-jsonl-from-klorb-server.md) for
why ACP, and the [plan 016 overview](../plans/ready/plan-016-acp-client-server.md) for the
full architecture this checkpoint is the first increment of (protocol mapping, threading model,
extensibility rules, the increments still to come).

At this checkpoint the server supports `initialize`, `session/new`, `session/prompt` with
streamed response and thinking text, and `session/cancel` — a prompt against a
plain-conversation model (no tool calls, no permission asks) is fully usable end to end. Tool
calls execute but produce no `session/update` for them yet; every permission `"ask"` verdict
fails closed, since no `on_permission_ask` callback is wired up yet either. Both land in a later
increment (see "Out of scope" below).

## Wire protocol

* Standard ACP: one JSON-RPC 2.0 object per line on stdin (requests/notifications *to* the
  server) and stdout (responses/notifications *from* the server), exactly as the protocol
  itself specifies. klorb never touches this framing directly — the `agent-client-protocol`
  Python SDK (import name `acp`, pinned `>= 0.7.0, < 0.8.0`) owns it.
* `initialize` negotiates the protocol version (klorb always replies with `acp.PROTOCOL_VERSION`,
  the SDK's own current value) and exchanges capabilities. klorb's `agentCapabilities._meta`
  carries `{"klorb": {}}` — an empty envelope today, grown by later increments as `_klorb/*`
  extension methods are added (see "Extension methods" below).
* `session/new` builds a fresh `Session` for the given `cwd`, tearing down any existing one
  first — see "Single top-level session" below. `mcpServers` is accepted but never acted on
  (klorb has no MCP support).
* `session/prompt` sends one turn. Only `TextContentBlock` prompt content is supported at this
  checkpoint — an image/audio/resource block gets a JSON-RPC `invalid params` error instead of
  being silently dropped or misread. The reply's `stopReason` is `"end_turn"` on success or
  `"cancelled"` if the turn was aborted via `session/cancel`; any other failure propagates as a
  JSON-RPC error, with the turn's error state left inside `Session` exactly as `send_turn()`
  already leaves it for the TUI/one-shot paths.
* `session/cancel` sets the active turn's cancel event, if one is running for that session —
  the same `threading.Event` mechanism Escape uses in the TUI. A no-op if the named session
  isn't the live one, or no turn is in flight.
* Exactly one `session/prompt` may be in flight at a time: a second one while a turn is running
  gets a JSON-RPC error rather than being queued — mid-turn message delivery is a future
  increment's `_klorb/enqueueMessage` extension method, not silent queueing here.
* A `session/prompt`/`session/cancel` naming a `sessionId` that isn't the current live session
  gets a JSON-RPC `invalid params` error.
* EOF on stdin (the client disconnects) closes any live session and exits `0`.

### Extension methods

None exist yet — `agentCapabilities._meta.klorb` is `{}`. Every `_klorb/*` request
`KlorbAcpAgent` doesn't recognize gets the standard `-32601` method-not-found error; every
unrecognized `_klorb/*` *notification* is silently ignored, per ACP's own extensibility rules.
Later increments (`plan-016-007` and on) grow this section with each extension method's exact
name, params/result shape, and capability flag as they land.

## How it works

`klorb.server` (`klorb/src/klorb/server/`) holds the library logic, independent of the CLI:

* `klorb.server.acp_server.ServerStreams` owns the async `StreamReader`/`StreamWriter` pair an
  ACP connection is built from. `ServerStreams.from_stdio()` is the **only** place real process
  stdio is bound (via the SDK's own `acp.stdio_streams()` helper); every other construction —
  tests, a future websocket transport — injects its own reader/writer pair through the
  constructor directly. This is what keeps `AcpServer` itself transport-agnostic: a websocket
  (or any other) transport is just a second factory on this same class.
* `klorb.server.acp_server.AcpServer` is constructed with a `ServerStreams` and a
  `ProcessConfig` — the template every `session/new` request's `SessionConfig` is copied from.
  It builds one `KlorbAcpAgent` (exposed read-only via `.agent`, for a test harness to inspect
  the live `Session` through) and runs it over the SDK's `acp.run_agent()` until the client
  disconnects, then closes any live session and returns `0`. There is no error condition here
  that produces a non-zero return — a malformed or unrecognized request becomes a JSON-RPC
  error reply, handled by the SDK's own connection machinery, not a process failure.
* `klorb.server.klorb_agent.KlorbAcpAgent` implements the ACP `Agent` protocol
  (`acp.Agent`, a `Protocol` — klorb subclasses it directly for a type-checked implementation,
  which is why every protocol method needs an override, even the ones this checkpoint doesn't
  support: mypy treats an explicitly-subclassed protocol's members as abstract). It owns:
  * The `ApiProvider`/`ModelRegistry` — constructed once (or injected, for tests) and reused
    across every `session/new` replacement, mirroring how [[terminal-repl]]'s `/clear` reuses
    `Session.provider`/`Session.model_registry` rather than rebuilding them.
  * At most one live `Session`, plus a stable `self._acp_session_id` snapshotted from
    `Session.id` the moment `session/new` builds it. Every later `session/prompt`/
    `session/cancel` is validated against this stable id, **not** the live `Session.id` — the
    session-naming classifier renames `Session.id` in place on the session's first turn (see
    docs/specs/session-and-turns.md's "Session naming" section), but the identity a client
    keeps addressing requests to has to stay fixed for the session's lifetime regardless.
  * `initialize()`: negotiates `protocolVersion` (always replies `acp.PROTOCOL_VERSION`) and
    returns `agentCapabilities._meta = {"klorb": {}}`.
  * `new_session(cwd, mcp_servers)`: resolves a `Workspace` for `cwd` (`TrustManager.
    resolve_workspace()`, the same ancestor-search/registration-lookup the CLI uses), copies it
    onto a fresh `SessionConfig` (`process_config.session.model_copy()`), builds a `ToolRegistry`
    via `ToolRegistry.discover_tools()`, closes any existing `Session`, and constructs the new
    one. `permission_framework` is left at the `SessionConfig` default (`"ask"`) — with no
    `on_permission_ask` callback wired up yet, every ask fails closed, which is the same net
    effect a `"deny"` policy would have (see docs/specs/permissions.md).
  * `prompt(prompt, session_id)`: validates `session_id`, rejects a second concurrent prompt,
    concatenates the request's `TextContentBlock`s (any other block type is a JSON-RPC error),
    and delegates to `TurnBridge.run_turn()`.
  * `cancel(session_id)`: sets `Session.active_cancel_event`, if one is live for that session.
  * `close()`: closes the live session, if any — called once by `AcpServer.run()` when the
    client disconnects.
* `klorb.server.turn_bridge.TurnBridge` is the sync/async bridge one `Session` instance keeps
  for its whole lifetime: `run_turn(prompt_text) -> str` (async) runs `Session.send_turn()` on a
  worker thread via `asyncio.to_thread()`, keeping the event loop free to service concurrent
  ACP requests (`session/cancel`) while a turn is in flight. It wires only `on_chunk` →
  `acp.update_agent_message_text()` and `on_thinking_chunk` → `acp.update_agent_thought_text()`
  at this checkpoint, plus a fresh `threading.Event` per turn as `cancel_event` — later
  increments add handlers to this one class rather than new plumbing. **Ordering guarantee:**
  every callback fires on the worker thread and is enqueued onto one `asyncio.Queue` via
  `loop.call_soon_threadsafe`; a single pump task awaits each `session/update` send in exactly
  the order the callbacks fired. One queue and one pump task per turn is what makes this an
  actual guarantee rather than an accident of scheduling — independently-scheduled coroutines
  wouldn't preserve order. `run_turn()` always drains and stops the pump task in a `finally`,
  whether `send_turn()` succeeds, raises `klorb.api_provider.ResponseAborted` (a cancelled
  turn), or raises anything else — the exception always propagates to the caller only after the
  pump has fully drained, so no queued update is ever lost or reordered around it.

### Single top-level session

The server supports exactly **one live `Session` at a time**, matching klorb's current
singleton-session reality. `session/new` tears down any existing session (`Session.close()`)
and builds a fresh one — the same semantics as the TUI's `/clear`. `loadSession` isn't
advertised (`AgentCapabilities.load_session` stays at its SDK default of `False`), so a
compliant client never calls `session/load`; `KlorbAcpAgent` still implements it (and every
other protocol method this checkpoint doesn't support — `list_sessions`, `set_session_mode`,
`set_session_model`, `authenticate`, `fork_session`, `resume_session`, `ext_method`) as an
explicit `RequestError.method_not_found`, rather than relying on the Protocol base class's
inherited no-op, so an unexpected call fails loudly instead of silently returning nothing.
`ext_notification` is the one exception: an unrecognized extension *notification* is ignored,
per ACP's own extensibility rules. Multiple top-level sessions and subagent child sessions are
explicitly future work — see the plan overview.

### CLI wiring

`klorb.cli.run_server_cli(argv)` parses `klorb server`'s own flags (`--config`), resolves it
through the same `load_process_config()` file stack every other subcommand reads (see
`run_show_config_cli()`), logging any `process_config.config_warnings` as warnings, then runs
`AcpServer(ServerStreams.from_stdio(), process_config)` to completion via `asyncio.run()` — the
resolved `ProcessConfig` genuinely shapes every session the server builds now, unlike the old
JSONL stub where `--config` existed only for validation. `klorb.cli.main()` recognizes `klorb
server ...` the same way it recognizes the other subcommands (`init`, `system-prompt`,
`models`, `show-config`): only when `server` is literally `sys.argv[1]`, checked before the
normal one-shot/REPL `argparse` parser runs.

### SIGINT handling

`klorb server` installs no custom `SIGINT` handler. A `SIGINT` (Ctrl-C, or an external `kill
-INT`) delivered while the server is blocked reading stdin is left to the interpreter's
ordinary `KeyboardInterrupt`, which unwinds `asyncio.run()` the same way it would for any other
Python script; `klorb.cli.run_server_cli()` catches it at that one point purely to exit with
status 0 instead of letting a traceback print to stderr.

## Usage

```bash
klorb server
```

```text
> {"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{}}}
< {"jsonrpc":"2.0","id":0,"result":{"agentCapabilities":{"_meta":{"klorb":{}}},"protocolVersion":1}}
> {"jsonrpc":"2.0","id":1,"method":"session/new","params":{"cwd":"/path/to/workspace","mcpServers":[]}}
< {"jsonrpc":"2.0","id":1,"result":{"sessionId":"2026-07-24-01-41-divergent-limpet"}}
> {"jsonrpc":"2.0","id":2,"method":"session/prompt","params":{"sessionId":"2026-07-24-01-41-divergent-limpet","prompt":[{"type":"text","text":"What is 2+2?"}]}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"2026-07-24-01-41-divergent-limpet","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"4"}}}}
< {"jsonrpc":"2.0","id":2,"result":{"stopReason":"end_turn"}}
```

(The `session/prompt` round trip above shows the shape a real streamed reply takes — one
`session/update` notification per chunk, then the request's own result — rather than a literal
captured transcript, since it depends on a live model call; `initialize`/`session/new` above
are a real captured transcript. `klorb/tests/klorb/server/test_acp_server_core.py` exercises
the full streaming path end to end against a scripted provider.)

## Out of scope

* Tool calls execute (when a session has tools configured — see docs/specs/tool-framework.md)
  but produce no `tool_call`/`tool_call_update` session updates yet — the model still gets its
  results, but an ACP client can't see them happen. Lands in `plan-016-003`.
* Every `"ask"` permission verdict fails closed: no `on_permission_ask` callback is wired
  through `TurnBridge` yet, so a tool call that would prompt interactively in the TUI is denied
  instead. Lands in `plan-016-005` (`session/request_permission`).
* `AskUserQuestions` and `EscalatePrivileges` calls fail closed the same way, for the same
  reason — no callback wired yet. Land in `plan-016-005`/`plan-016-007`.
* No session modes (`session/set_mode`), model/thinking config options, session naming/
  token-usage updates, or workspace-trust bridging — `permission_framework` is fixed at
  `"ask"` for the lifetime of a session created through this server. Lands in `plan-016-008`.
* No chainlink task-plan (`session/update` → `plan`) updates. Lands in `plan-016-010`.
* No mid-turn message queueing (`_klorb/enqueueMessage`) — a second `session/prompt` while one
  is in flight is a JSON-RPC error, not queued. Lands in `plan-016-012`.
* No websocket (or other non-stdio) transport — `ServerStreams.from_stdio()` is the only
  factory today, though `AcpServer` itself doesn't know or care which one built its streams.
* No multiple concurrent top-level sessions, and no subagent child sessions.
