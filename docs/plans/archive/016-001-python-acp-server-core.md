# Plan 016, increment 001: Python ACP server core

> **Draft.** Part of [Plan 016](plan-016-acp-client-server.md). Do not implement from the
> `drafting/` folder.

## Goal

Replace `klorb.server.jsonl_server.JsonlServer` with `AcpServer`, a real Agent Client
Protocol server behind `klorb server`. At this checkpoint the server supports: `initialize`,
`session/new`, `session/prompt` with streamed response and thinking text
(`agent_message_chunk` / `agent_thought_chunk` session updates), and `session/cancel`. Tool
calls execute but produce no tool-call updates yet (increment 003) and every permission ask
fails closed (increment 005) — a prompt against a plain-conversation model is fully usable
end to end.

## Dependencies / prerequisites

* New runtime dependency: `agent-client-protocol >= 0.7.0, < 0.8.0` (import name `acp`).
  Follow `.claude/skills/add-python-dependency/SKILL.md` (pyproject + Makefile lock-file
  regeneration). This SDK is pydantic-2-based, matching klorb.
* Read the pinned SDK's actual API before coding: `acp.Agent` (async base class),
  `acp.schema` (generated protocol models), `acp.helpers` (update builders), and the
  connection class that binds an agent implementation to a reader/writer pair. This plan
  names methods per SDK 0.7.x (`initialize`, `new_session`, `prompt`, `cancel`,
  `session_update`, ...); if the pinned version differs, follow the SDK and note the
  deviation in the spec update.

## Deliverables

### 1. `ServerStreams` (in `klorb/src/klorb/server/acp_server.py`)

A small class owning the transport endpoints the ACP connection is built from — the async
readable and writable the SDK connection needs (whatever concrete pair the pinned SDK's
connection constructor takes; likely `asyncio.StreamReader`/`asyncio.StreamWriter` or
anyio-style streams).

* `ServerStreams.from_stdio() -> ServerStreams` — the **only** place real stdio is bound
  (wrap `sys.stdin.buffer`/`sys.stdout.buffer` via `loop.connect_read_pipe`/
  `connect_write_pipe` or the SDK's own stdio helper if it exposes the pieces separately).
* Constructor takes the endpoints explicitly so tests (and a future websocket transport)
  inject their own. No module-level globals; per AGENTS.md, state lives in the class.

### 2. `KlorbAcpAgent` (`klorb/src/klorb/server/klorb_agent.py`)

Subclasses `acp.Agent`. Owns at most one live `Session` (`self._session: Session | None`)
plus the `ProcessConfig` resolved at server start.

* `initialize(...)`: negotiates `protocolVersion` (echo the SDK's current
  `PROTOCOL_VERSION`), returns agent capabilities. Advertise only what exists at this
  increment; include `agentCapabilities._meta = {"klorb": {}}` as the (empty, for now)
  custom-capability envelope later increments grow. Record the client's capabilities on the
  agent instance for later increments.
* `new_session(cwd, mcp_servers, ...)`: builds a `SessionConfig` the same way
  `klorb.cli.main()` does for an interactive run — `load_process_config()` layer stack, with
  the workspace resolved from `cwd` via the same `find_workspace_root()` path — then
  constructs a `Session` with a fresh `ToolRegistry`. `permission_framework` starts `"ask"`
  (the client is an interactive surface; asks fail closed until 005 wires
  `on_permission_ask`, which is the same net behavior as `"ask"`-without-callback that a
  callbackless `send_turn` already has). If a session already exists, `Session.close()` it
  first and replace it — `/clear` semantics, per the plan's single-session policy. Returns
  the new `Session.id` as the ACP `sessionId`. Ignore `mcp_servers` (klorb has no MCP
  support; that's fine — it's a list we simply don't act on).
* `prompt(session_id, prompt, ...)`: validates `session_id` against the live session (JSON-RPC
  error on mismatch), extracts the text content blocks (concatenate `text` blocks; reject
  image/audio blocks with a clear error — not supported yet), and delegates to the
  `TurnBridge` (below). Returns `PromptResponse(stop_reason=...)`: `"end_turn"` on success,
  `"cancelled"` if the turn raised `ResponseAborted`. Any other exception propagates as a
  JSON-RPC error (the SDK handles the encoding); the turn's error state inside `Session` is
  exactly what `send_turn` already leaves behind.
* `cancel(session_id)`: sets the active turn's cancel event (no-op if no turn is running).
* One prompt at a time: a second `session/prompt` while one is in flight gets a JSON-RPC
  error (ACP clients are required not to do this; fail loudly, don't queue silently —
  mid-turn user messages are increment 012's ext method).

### 3. `TurnBridge` (`klorb/src/klorb/server/turn_bridge.py`)

The sync↔async bridge described in the plan overview ("Threading bridge") — implement exactly
that shape:

* `run_turn(prompt_text) -> str` (async): builds the `TurnEventHandlers` for this turn,
  starts the sender task, runs `session.send_turn(...)` via `asyncio.to_thread`, and always
  drains/stops the sender task in a `finally`.
* This increment wires only: `on_chunk` → `agent_message_chunk`, `on_thinking_chunk` →
  `agent_thought_chunk` (both via `acp.helpers` builders on the ordered outbound queue), and
  `cancel_event` (a fresh `threading.Event` per turn, exposed so `cancel()` can set it).
  Later increments add handlers to this one class rather than new plumbing.
* Ordering guarantee: one `asyncio.Queue` + one pump task per turn; callbacks enqueue via
  `loop.call_soon_threadsafe(queue.put_nowait, item)`. The pump awaits the SDK's
  `session_update` send for each item in FIFO order.

### 4. `AcpServer` (`klorb/src/klorb/server/acp_server.py`)

* Constructed with a `ServerStreams` and a `ProcessConfig`. `run() -> int` (async, with a
  small sync wrapper for the CLI): builds `KlorbAcpAgent`, binds it to the SDK connection
  over the streams, and serves until the client disconnects (EOF), then closes any live
  session (`Session.close()`) and returns 0. `logger.debug` breadcrumbs at start/stop,
  session create/replace/close, and turn start/end (per AGENTS.md's logging rule).
* The old stub JSONL protocol (`greet`/`shutdown`) is erased outright, with no
  compatibility handling — a client speaking it just gets the SDK's standard JSON-RPC
  error replies.

### 5. CLI + cleanup

* `klorb.cli.run_server_cli()` now resolves `ProcessConfig` (as today) and runs
  `AcpServer(ServerStreams.from_stdio(), process_config)`. `--config` keeps its meaning and
  is no longer a no-op: the config actually shapes the sessions the server builds.
* Delete `klorb/src/klorb/server/jsonl_server.py` and
  `klorb/tests/klorb/server/test_jsonl_server.py`. Update `klorb/server/__init__.py`
  re-exports.
* Rewrite `docs/specs/klorb-server.md` as the current-state spec of the ACP server (protocol
  summary, class layout, threading bridge, single-session policy, extension-method registry —
  empty for now). Add an ADR `docs/adrs/speak-acp-not-bespoke-jsonl-from-klorb-server.md`
  recording the protocol/library decision (date/question/answer/reasoning, per AGENTS.md).

## Tests (`klorb/tests/klorb/server/`)

* `acp_harness.py`: builds an in-process pair — `AcpServer` on one end, the SDK's
  *client-side* connection on the other, over in-memory duplex streams
  (`asyncio.StreamReader` fed by the server's writer, or the SDK's own in-memory transport
  if it ships one). Exposes a `HarnessClient` implementing the SDK `Client` interface that
  records every `session_update` it receives, for assertions. Reuse the scripted-`ApiProvider`
  mock pattern from `tests/klorb/session/test_session.py` for the `Session` underneath.
* `test_acp_server_core.py`, covering at minimum:
  * initialize → capabilities round-trip (protocol version echoed; `_meta.klorb` present).
  * new_session returns the live `Session.id`; a second new_session closes the first session
    (assert via `Session.close()` observable state) and returns a different id.
  * prompt streams: scripted provider emits chunks + thinking; harness client observes
    `agent_thought_chunk` then `agent_message_chunk` updates in order, then the prompt
    response with `stopReason: "end_turn"`.
  * update ordering: a script that interleaves thinking/content chunks arrives in exactly
    the fired order.
  * cancel: script blocks mid-stream (event-gated side effect); `session/cancel` →
    prompt resolves `stopReason: "cancelled"`; session history keeps the aborted turn
    (assert `processing_state == "aborted"` — proving the server drives the session's
    existing abort path, not re-proving the path itself).
  * prompt with wrong sessionId → JSON-RPC error; second concurrent prompt → error.
  * EOF/disconnect → `run()` returns 0 and the session is closed.
* `test_cli.py`: extend the existing `server` subcommand test(s) to assert the new wiring
  (construction with `from_stdio` streams can be monkeypatched; don't spawn a real server).

## Checkpoint criteria

* `make -C klorb lint typecheck test` green.
* Manual: `klorb server` from a terminal, paste
  `{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{}}}`
  and friends; observe a full prompt turn streaming `session/update` notifications. Include
  this recipe (with working JSON lines) in the rewritten spec's Usage section.
* The VS Code stub still builds; its greet round-trip now surfaces a protocol error in the
  panel (acceptable until increment 002 lands).
