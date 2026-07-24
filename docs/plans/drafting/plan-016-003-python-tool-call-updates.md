# Plan 016, increment 003: Python tool-call session updates

> **Draft.** Part of [Plan 016](plan-016-acp-client-server.md). Do not implement from the
> `drafting/` folder.

## Goal

Every tool call a turn makes is reported to the ACP client as it happens:
`TurnEventHandlers.on_tool_call_started` becomes a `session/update` → `tool_call`
notification and `on_tool_call` becomes `tool_call_update` — with ACP tool kinds, file
locations, human-readable titles, and diff content for the edit tools. After this increment a
stock ACP client shows klorb's tool activity live; the klorb VS Code rendering lands in 004.

## Deliverables

### 1. Mapping functions (`klorb/src/klorb/server/update_mapping.py`)

Pure functions (no I/O, no Session access) covering:

* `tool_call_started_update(event: ToolCallStartedEvent) -> <acp tool_call update>`:
  * `toolCallId` = `event.call_id`; `status` = `"in_progress"` (klorb fires the started
    event immediately before `apply()` runs — there is no separate pending phase worth
    reporting).
  * `title` = the tool's pre-execution summary — instantiate the named tool via the
    server's `ToolRegistry` and call `summary(args)` with no result, exactly the way
    `RunningToolCallStatic` gets its label in the TUI (fall back to
    `default_tool_call_summary()` for an unregistered name). Plain text, single line.
  * `kind` from a module-level `TOOL_KIND_MAP: dict[str, ToolKind]` keyed by klorb tool
    name: `ReadFile`/`ReadMemory`/`ReadScratchpad`/`ReadSkillFile` → `read`;
    `EditFile`/`ReplaceAll`/`CreateFile`/`EditMemory`/`CreateMemory`/`EditScratchpad` →
    `edit`; `Grep`/`FindFile`/`ListDir`/`SearchMemories`/`SearchScratchpad`/`SearchSkills`
    /`ListMemories` → `search`; `Bash` → `execute`; `WebFetch` → `fetch`;
    `TodoList`/`TodoNext`/`TodoCreate`/`TodoUpdate`/`ActivateSkill` → `think`;
    everything else (incl. `AskUserQuestions`/`EscalatePrivileges`) → `other`. Unknown
    names → `other`.
  * `locations`: for tools whose args name a filesystem path, emit
    `[{path: <absolute path>}]` — resolve relative args against the session's workspace
    root with the same helper the tools themselves use. Add a
    `TOOL_LOCATION_ARG: dict[str, str]` (tool name → arg key, e.g. `ReadFile: "path"`,
    `EditFile: "path"`, `CreateFile: "path"`, `Grep`/`FindFile`/`ListDir`: `"path"`).
    `ReadFile` with a start line also sets `line`. Tools without a path arg emit no
    locations.
  * `rawInput` = `event.args`.
* `tool_call_finished_update(event: ToolCallEvent, session) -> <acp tool_call_update>`:
  * `status` = `"failed"` when `event.error` is set, else `"completed"`.
  * `content`:
    * Edit-family tools whose result carries `result["diff"]` hunks (see
      docs/specs/terminal-repl.md "Diff and read previews"): emit one ACP `diff` content
      block per touched file — `path` from the call args, `oldText` = the hunks'
      `context`+`del` lines joined, `newText` = the hunks' `context`+`add` lines joined.
      This is a hunk-reassembled approximation of the file (klorb persists hunks, not whole
      files — deliberately, see docs/adrs/persist-diff-hunks-in-edit-result.md); record
      that approximation as a note in the spec, and additionally put the raw hunks under
      the block's `_meta.klorb.diffHunks` so the klorb client can render a real gutter
      view in 004.
    * Everything else: one text content block with the tool's `detail_view()` output
      (instantiate-and-render, falling back to `default_tool_call_detail()`), the same
      string the TUI's Ctrl+O detail shows. On failure, the text block is the error string
      (`event.error`), plus `raw_arguments` when set (malformed-JSON case).
  * `rawOutput`: the JSON-safe form of `event.result` (reuse the JSON encoding the
    tool-response envelope already applies; omit when not JSON-serializable).
* Keep every mapping total: no klorb event may raise out of the mapping layer; on any
  per-field failure, degrade to a text block and `logger.debug` the reason.

### 2. `TurnBridge` wiring

Register `on_tool_call_started` and `on_tool_call` in the bridge's `TurnEventHandlers`,
enqueueing the mapped updates on the same ordered outbound queue as chunks — so the
started-update, that round's chunks, and the finished-update interleave exactly in fired
order (the property increment 001's queue already guarantees).

### 3. Docs

Extend `docs/specs/klorb-server.md`: the tool-call update mapping (kind table, location
table, diff-block strategy + `_meta.klorb.diffHunks`), and the totality/degradation rule.

## Tests

* `test_update_mapping.py` (pure, no harness): kind map covers every registered tool name
  (parametrize over `ToolRegistry.discover_tools()` so a future tool without a mapping
  fails the test with a clear message rather than silently becoming `other` — an explicit
  `_UNMAPPED_OK` allowlist keeps intent visible); location extraction incl. relative-path
  resolution and the no-path case; diff-block reassembly from representative hunks
  (add-only, del-only, mixed, multi-hunk); failure mapping (error + raw_arguments);
  non-serializable result degradation.
* `test_acp_server_tool_calls.py` (harness from 001): scripted provider issues a turn with
  two tool calls (one succeeding `ReadFile`-style, one failing) against a real
  `ToolRegistry` in a tmp workspace; assert the client observes
  `tool_call(in_progress)` → `tool_call_update(completed|failed)` pairs matched by
  `toolCallId`, ordered correctly relative to the surrounding message chunks. (Real tools
  here prove the registry/summary integration; the mapping edge cases stay in the pure
  tests.)

## Checkpoint criteria

* `make -C klorb lint typecheck test` green.
* Manual: drive `klorb server` by hand (001's recipe) with a prompt that triggers a
  `ReadFile`/`ListDir`; observe `tool_call`/`tool_call_update` notifications. The VS Code
  panel (002 state) still works — it ignores the new update kinds it doesn't yet render.
