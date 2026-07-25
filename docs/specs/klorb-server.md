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
streamed response, thinking text, and tool-call activity, `session/cancel`, and
`session/request_permission` for both an ordinary permission `"ask"` verdict and an
`EscalatePrivileges` request — a prompt against a plain-conversation model, one that calls
tools, or one whose tools need interactive approval, is fully usable end to end. `AskUserQuestions`
still fails closed, since no `on_ask_user_questions` callback is wired up yet — lands in
`plan-016-007` (see "Out of scope" below).

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

`agentCapabilities._meta.klorb` is `{}` — no *agent*-advertised extension method exists yet.
Every `_klorb/*` request `KlorbAcpAgent` doesn't recognize gets the standard `-32601`
method-not-found error; every unrecognized `_klorb/*` *notification* is silently ignored, per
ACP's own extensibility rules. Later increments (`plan-016-007` and on) grow the agent side of
this section as they land.

One *client*-advertised extension method exists, called server → client:

* **`_klorb/raiseToolCallLimit`** — sent when a turn's `max_tool_calls_per_turn`/
  `max_tool_calls_per_session` cap is reached (see docs/specs/session-and-turns.md's tool-call
  cap section). Params: `{sessionId: string, message: string}` (`message` is the same
  human-readable cap-reached prompt `Session._confirm_limit_increase` builds for the TUI's
  `ToolCallLimitScreen`). Result: `{approved: boolean}` — `true` doubles the reached cap and lets
  the call proceed, exactly as the TUI's confirmation does. `TurnBridge` calls this only when the
  client advertised `clientCapabilities._meta.klorb.raiseToolCallLimit = true` at `initialize`;
  otherwise `on_tool_call_limit_reached` returns `False` immediately with no wire traffic at all,
  and the turn fails with `ToolCallLimitExceeded` (the same fail-closed behavior a headless
  one-shot run already has).

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
  ACP requests (`session/cancel`) while a turn is in flight. It wires `on_chunk` →
  `acp.update_agent_message_text()`, `on_thinking_chunk` → `acp.update_agent_thought_text()`, and
  `on_tool_call_started`/`on_tool_call` → `klorb.server.update_mapping.
  tool_call_started_update()`/`tool_call_finished_update()` (passing this turn's
  `Session.tool_registry`/`Session.config.workspace.path` through — see "Tool-call update
  mapping" below), plus a fresh `threading.Event` per turn as `cancel_event` — later increments
  add handlers to this one class rather than new plumbing. `on_tool_call_started`/`on_tool_call`
  additionally push/pop `event.call_id` on a per-turn stack, so a same-turn permission/escalation
  ask can link itself to whichever call is currently in flight — see "Permission asks and
  escalation" below. **Ordering guarantee:** every callback fires on the worker thread and is
  enqueued onto one `asyncio.Queue` via `loop.call_soon_threadsafe`; a single pump task awaits
  each `session/update` send in exactly the order the callbacks fired. One queue and one pump
  task per turn is what makes this an actual guarantee rather than an accident of scheduling —
  independently-scheduled coroutines wouldn't preserve order. `run_turn()` always drains and
  stops the pump task in a `finally`, whether `send_turn()` succeeds, raises `klorb.api_provider.
  ResponseAborted` (a cancelled turn), or raises anything else — the exception always propagates
  to the caller only after the pump has fully drained, so no queued update is ever lost or
  reordered around it. `on_permission_ask`/`on_escalate_privileges`/`on_tool_call_limit_reached`
  are blocking asks rather than fire-and-forget notifications: each builds its
  `session/request_permission`/`_klorb/raiseToolCallLimit` round trip as a coroutine and runs it
  via `asyncio.run_coroutine_threadsafe(...).result()`, first `await`ing `queue.join()` inside
  that coroutine so every `session/update` already enqueued has actually been sent before the
  ask goes out on the wire — the same ordering guarantee, extended to asks, so a permission
  prompt never overtakes the `tool_call` update that explains what it's about.

### Tool-call update mapping

`klorb.server.update_mapping` holds the pure functions (no I/O beyond read-only path
canonicalization, no live `Session`/ACP connection needed) that turn a klorb tool-call event into
an ACP `session/update`: `tool_call_started_update(event, tool_registry, workspace_root)` maps
`klorb.session.events.ToolCallStartedEvent` to a `tool_call` update, and
`tool_call_finished_update(event, tool_registry, workspace_root)` maps `ToolCallEvent` to a
`tool_call_update`. Both take the turn's `ToolRegistry` (or `None`) and workspace root `Path`
directly rather than a live `Session`, so they're callable from a test with just
`ToolRegistry.discover_tools()` and a workspace path — no ACP harness, no `Session` construction.

* **Started update** (`status="in_progress"` unconditionally — klorb fires
  `on_tool_call_started` immediately before `apply()` runs, so there's no separate `"pending"`
  phase worth reporting):
  * `title` — the tool's pre-execution summary, via `Tool.summary(args)` with no result/error
    (the same string `RunningToolCallStatic` shows in the TUI), falling back to
    `default_tool_call_summary()` for a name the turn's `ToolRegistry` doesn't have (unregistered,
    or no registry at all).
  * `kind` — from `TOOL_KIND_MAP: dict[str, ToolKind]`, keyed by klorb tool name:

    | `ToolKind` | Tools |
    | --- | --- |
    | `read` | `ReadFile`, `ReadMemory`, `ReadScratchpad`, `ReadSkillFile` |
    | `edit` | `EditFile`, `ReplaceAll`, `CreateFile`, `EditMemory`, `CreateMemory`, `EditScratchpad` |
    | `search` | `Grep`, `FindFile`, `ListDir`, `SearchMemories`, `SearchScratchpad`, `SearchSkills`, `ListMemories` |
    | `execute` | `Bash` |
    | `fetch` | `WebFetch` |
    | `think` | `TodoList`, `TodoNext`, `TodoCreate`, `TodoUpdate`, `ActivateSkill` |
    | `delete` | `ForgetMemory` |
    | `other` | `AskUserQuestions`, `EscalatePrivileges` |

    A name this table doesn't cover (a future tool added without an entry) falls back to
    `"other"` at lookup time — `tests/klorb/server/test_update_mapping.py` parametrizes over
    every tool `ToolRegistry.discover_tools()` currently returns so that omission fails the test
    loudly instead of passing silently.
  * `locations` — `[{path, line}]` for a tool whose call names a filesystem path, resolved
    against `workspace_root` via `klorb.permissions.directory_access.canonicalize_dir()` (the
    same canonicalization primitive the file tools themselves use via `canonicalize_candidate()`
    under the hood). `TOOL_LOCATION_ARG: dict[str, str]` names which arg key holds the path, per
    tool — `ReadFile`/`EditFile`/`CreateFile`: `filename`; `Grep`: `path`; `FindFile`/`ListDir`:
    `dirname`. (Every one of these tools names its path argument something other than a
    uniform `"path"` key; `TOOL_LOCATION_ARG`'s values reflect each tool's actual argument name,
    not a normalized one.) `ReadFile`'s `start_line` arg additionally sets `line` on the one
    location reported. A tool not in this table (including `EditScratchpad`, whose subject is
    harness-managed and never a model-nameable path) emits no `locations` at all.
  * `rawInput` — `event.args`, unchanged (already a JSON-safe dict, parsed from the model's tool
    call).
* **Finished update**: `status` is `"failed"` when `event.error` is set, else `"completed"`.
  * `content`, on success: an edit-family call whose result carries diff hunks (`Tool.
    diff_preview()` returns non-`None`) reports one ACP `diff` content block —
    `oldText`/`newText` reassembled from the hunks' `context`+`del`/`context`+`add` lines (`None`
    `oldText` for a brand-new file/memory/scratchpad, matching ACP's own convention). This is a
    hunk-reassembled *approximation* of the touched file, not its literal full contents — klorb
    persists hunks, not whole files, by design (see
    docs/adrs/persist-diff-hunks-in-edit-result.md) — so the raw hunks additionally ride under
    the block's `_meta.klorb.diffHunks` for a client that wants to render a real gutter view
    (lands in `plan-016-004`) instead of reassembled text. The block's `path` is
    `args["filename"]` (resolved against `workspace_root`) for every edit-family tool that has
    one, or the tool's own name for `EditScratchpad`. Every other successful call reports one
    text content block with the tool's `detail_view()` output (instantiate-and-render, falling
    back to `default_tool_call_detail()`) — the same string the TUI's Ctrl+O detail shows.
  * `content`, on failure (regardless of tool identity): one text content block with the error
    string (`event.error`), plus `event.raw_arguments` when set — the malformed-JSON case, where
    no tool ever ran.
  * `rawOutput` — `event.result`, unchanged, unless it isn't JSON-serializable (checked via
    `json.dumps()`), in which case it's omitted and the reason logged at `debug`.
* **Totality**: every function in `update_mapping.py` is total — no klorb event may raise a
  mapping failure out to the caller. A per-field failure (a tool's `summary()`/`detail_view()`/
  `diff_preview()` override raising, or an unresolvable location) degrades to a simpler
  rendering (a default summary/detail string, or no location/diff content) rather than
  propagating, with the reason logged at `debug` level.

### Permission asks and escalation

`Session.send_turn()`'s `on_permission_ask`/`on_escalate_privileges` callbacks (see
docs/specs/permissions.md's "Interactive `"ask"` confirmation" section and
docs/specs/session-and-turns.md) are both wired through `TurnBridge` onto the single ACP
`session/request_permission` request — the same request shape both a permission ask and an
`EscalatePrivileges` ask use, distinguished by their `_meta.klorb` payload, so a stock
(non-klorb-aware) ACP client still gets a comprehensible approve/deny prompt for either. Neither
callback fires for a `"auto"`/`"deny"` `permission_framework` session — `Session` itself
short-circuits before ever calling back, exactly as it does for the TUI.

* **Tool-call linkage.** Every `session/request_permission` names the `tool_call:
  acp.schema.ToolCallUpdate` (just `toolCallId`, no other field) the ask belongs to:
  `klorb.server.update_mapping.permission_ask_tool_call_update(call_id, fallback_title)` returns
  `ToolCallUpdate(tool_call_id=call_id)` for the most recent call `TurnBridge`'s per-turn stack
  has in flight (pushed on `on_tool_call_started`, popped on `on_tool_call` — asks are raised
  from within `apply()`, so one is always live in practice), or, defensively, a freshly
  synthesized id titled `fallback_title` (the ask's own `resource_description`/`description`)
  when the stack is empty.
* **Permission-ask options** (`klorb.server.update_mapping.permission_ask_options`, pure —
  built from `PermissionAskContext.resource` alone): up to five fixed options, id encoding
  `<action>:<scope>` —

  | `optionId` | `kind` | Name | Always offered? |
  | --- | --- | --- | --- |
  | `allow:once` | `allow_once` | "Allow once" | yes |
  | `deny:once` | `reject_once` | "Deny" | yes |
  | `allow:session` | `allow_always` | "Allow for this session" | yes |
  | `allow:workspace` | `allow_always` | "Always allow (workspace)" | only if `resource.is_persistable` |
  | `allow:homedir` | `allow_always` | "Always allow (home config)" | only if `resource.is_persistable` |

  A `StructuralResource` item (`resource.is_persistable` is `False` — see
  docs/specs/permissions.md) offers only the first three: it names no filesystem path, command
  pattern, skill, or domain a workspace/homedir grant could be recorded against. This is a
  narrower surface than the TUI's own 2D grid (no persistent-scope `Deny`, no free-text "Other"
  row as a selectable option) — a client wanting the free-text redirect instead sets
  `_meta.klorb.otherText` on its response (below), and persistent-scope denial isn't offered at
  this checkpoint. Every option's own `_meta.klorb.scope` carries its `optionId`'s scope token,
  so a client can style/group options without parsing ids itself.
* **Escalation options** (`klorb.server.update_mapping.escalate_privileges_options`, fixed,
  no per-context data): exactly `allow:once` (`allow_once`, "Approve for this session") and
  `deny:once` (`reject_once`, "Deny") — an `EscalatePrivileges` grant is always session-scoped
  and revokes when the session ends, so there is no persistent-scope option to offer at all.
* **`_meta.klorb` on a permission-ask request**
  (`klorb.server.update_mapping.permission_ask_meta`): always `resourceDescription`. For a
  `BashTool` ask (`PermissionAskContext.bash_context` set): `commandText` (the full compound
  command), `itemCommandText` (this item's own statement — see
  docs/adrs/permission-ask-item-shows-its-own-command-text-not-the-full-compound.md),
  `itemIndex`/`itemTotal` (0-based, this item's position within its `sibling_items` batch —
  mirroring `AskUserQuestionsItemContext.index`/`.total`'s own convention), `grantPatterns`
  (`list[list[str]]`, the `commandRules` pattern a persistent grant would cover — the risk
  classifier's own `suggested_pattern` when it offered one, else the same deterministic
  literal-argv patterns `CommandResource.grant_preview()` computes; omitted entirely for a
  non-`CommandResource` bash item, e.g. a redirect or structural item), and `riskLevel` (the
  classifier's 0–10 `risk_score`, omitted when classification didn't run or failed).
* **`_meta.klorb` on an escalation request**
  (`klorb.server.update_mapping.escalate_privileges_meta`): `escalation.scope`/
  `escalation.description`, so the client can render this as its own distinct flow (e.g. a
  red-border panel) rather than an ordinary permission grid.
* **Risk classification** reuses `klorb.permissions.risk_classifier.
  resolve_item_risk_assessment(ctx, session=, process_config=)` directly — the same
  gating/sibling-batching/per-session-caching function `klorb.tui.mixins.interactions.
  InteractionsMixin._confirm_permission_ask` calls, whose own docstring calls out non-TUI reuse
  (e.g. "a future VSCode plugin") as the reason it lives outside `klorb.tui` in the first place.
  `TurnBridge.on_permission_ask` calls it synchronously — no `asyncio.to_thread` offload needed,
  unlike the TUI, since the callback already runs on `Session.send_turn()`'s own worker thread,
  off the event loop. On the first ask of a `sibling_items` batch this classifies every item in
  one request (`classify_command_risk()`); the cache (keyed in `Session.tool_state`) means the
  remaining items of the same compound command reuse that one report. Classifier failure (or
  `tools.bash.riskClassifier.enabled=false`) degrades to `risk=None`: `grantPatterns` falls back
  to the deterministic literal-argv computation, `riskLevel` is omitted, and the ask still
  proceeds. `TurnBridge.on_permission_ask` also calls `record_decision_history()` right after
  building the final decision, exactly as the TUI does, so a later item's/turn's classification
  in the same session can calibrate against it.
* **Decision mapping**
  (`klorb.server.update_mapping.permission_decision_from_outcome`/
  `escalate_privileges_decision_from_outcome`): a `cancelled` `RequestPermissionResponse.outcome`
  maps to `PermissionDecision(action="deny", scope="once")` / `EscalatePrivilegesDecision(
  approved=False)`. A `selected` outcome whose `_meta.klorb.otherText` is a non-empty string
  (regardless of which `optionId` was actually selected alongside it) maps to
  `PermissionDecision(action="deny", scope="once", other_text=...)` — the free-text redirect the
  TUI's panel supports. Otherwise the `optionId`'s own `<action>:<scope>` is split back into the
  decision directly (an unrecognized `optionId` raises `ValueError`, propagating as a turn
  failure — a compliant client only ever echoes back an id this request itself offered); an
  escalation `optionId` other than `allow:once` (including a client echoing back `deny:once`)
  denies. `PermissionDecision.grant_patterns` is threaded through unconditionally from the risk
  classifier's own suggestion (`None` when the classifier didn't suggest one, even if
  `grantPatterns`'s display value fell back to the deterministic computation) — mirroring the
  TUI's own unconditional threading — so a persistent grant this decision causes is recorded at
  exactly the pattern the client displayed, never silently recomputed to something else.
* **`permission_framework` interplay**: unchanged from docs/specs/permissions.md — `"auto"`/
  `"deny"` sessions never reach `TurnBridge`'s callbacks at all; only `"ask"` does.

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

A model turn that calls `Bash` under the default `permission_framework: "ask"` additionally
sends a `session/request_permission` request mid-turn — this is the request the client answers,
e.g. with `allow:session`, to let the command run:

```text
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"...","update":{"sessionUpdate":"tool_call","toolCallId":"call_1","title":"Run: ls","kind":"execute","status":"in_progress"}}}
< {"jsonrpc":"2.0","id":3,"method":"session/request_permission","params":{"sessionId":"...","toolCall":{"toolCallId":"call_1"},"options":[{"optionId":"allow:once","kind":"allow_once","name":"Allow once","_meta":{"klorb":{"scope":"once"}}},{"optionId":"deny:once","kind":"reject_once","name":"Deny","_meta":{"klorb":{"scope":"once"}}},{"optionId":"allow:session","kind":"allow_always","name":"Allow for this session","_meta":{"klorb":{"scope":"session"}}}],"_meta":{"klorb":{"resourceDescription":"run shell command: ls","commandText":"ls","itemCommandText":"ls","itemIndex":0,"itemTotal":1,"riskLevel":0,"grantPatterns":[["ls"]]}}}}
> {"jsonrpc":"2.0","id":3,"result":{"outcome":{"outcome":"selected","optionId":"allow:session"}}}
```

## Out of scope

* A stock ACP client doesn't yet render tool-call activity specially — the klorb VS Code
  plugin's own rendering (running animation, expandable detail, open-file/open-diff editor
  integration) lands in `plan-016-004`; this checkpoint only emits the `tool_call`/
  `tool_call_update` notifications themselves (see "Tool-call update mapping" above).
* A stock ACP client doesn't yet render a permission-ask option grid or an escalation panel
  specially — the klorb VS Code plugin's own approval panels (option grid, command preview,
  free-text "other", escalation styling) land in `plan-016-006`; this checkpoint only emits the
  `session/request_permission` request itself (see "Permission asks and escalation" above).
* No persistent-scope `Deny` option, and no free-text "Other" row as a selectable
  `PermissionOption` — a client wants the free-text redirect via `_meta.klorb.otherText` on its
  response instead (see "Permission asks and escalation" above); a genuinely first-class
  persistent-deny option may be revisited if a real client needs one.
* `AskUserQuestions` calls still fail closed: no `on_ask_user_questions` callback is wired
  through `TurnBridge` yet. Lands in `plan-016-007` (`_klorb/askUserQuestions`).
* No session modes (`session/set_mode`), model/thinking config options, session naming/
  token-usage updates, or workspace-trust bridging — `permission_framework` is fixed at
  `"ask"` for the lifetime of a session created through this server. Lands in `plan-016-008`.
* No chainlink task-plan (`session/update` → `plan`) updates. Lands in `plan-016-010`.
* No mid-turn message queueing (`_klorb/enqueueMessage`) — a second `session/prompt` while one
  is in flight is a JSON-RPC error, not queued. Lands in `plan-016-012`.
* No websocket (or other non-stdio) transport — `ServerStreams.from_stdio()` is the only
  factory today, though `AcpServer` itself doesn't know or care which one built its streams.
* No multiple concurrent top-level sessions, and no subagent child sessions.
