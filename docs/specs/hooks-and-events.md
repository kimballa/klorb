# Hooks and Events

## Summary

Hooks and events are policy overlays configured entirely through `klorb-config.json`: a hook
attaches a handler chain to a specific, planned lifecycle moment (a session starting, a tool
about to run, a turn ending); an event attaches a handler chain to an occurrence that happens on
its own schedule or trigger (a filesystem change, a timer, workspace trust changing). Both are
implemented in `klorb.hooks` (`klorb/src/klorb/hooks/`) and are inert by default — `hooks`/`events`
are empty in `klorb/src/klorb/resources/default-config.json`, so a user with no config for either
key sees no behavior change.

See `docs/user/hooks.md` for the user-facing config reference (available hooks/events, filter
syntax, handler-type examples). This spec covers the implementation: wire schema, dispatch/chain
semantics, config merge, and each lifecycle point's wiring.

## Configuration

`hooks` and `events` are flat, process-scoped keys in `PROCESS_KEY_MAP`
(`klorb/src/klorb/process_config.py`), top-level in `klorb-config.json` (not inside
`sessionDefaults`) — cross-cutting policy from the config stack, not something a session mutates
at runtime, and required to be identical across every concurrently running session the same way
every other `ProcessConfig` field is (docs/specs/process-and-session-config.md).

Each key's value is an object keyed by hook/event name (`onProcessStart`, `FileSystemModified`,
...), each holding a list of handler configs — `dict[str, list[HookConfig]]` for `hooks`,
`dict[str, list[EventConfig]]` for `events` (`ProcessConfig.hooks`/`ProcessConfig.events`).
`klorb.hooks.config.HOOK_NAMES`/`EVENT_NAMES` enumerate the recognized names; an entry under an
unrecognized name is dropped with a `config_warnings` entry rather than accepted silently.

An untrusted workspace's `.klorb/klorb-config.json` layer is skipped entirely by
`load_process_config()` whenever `workspace.trusted` is `False` (docs/specs/projects-and-trust.md),
so `hooks`/`events` need no dedicated enforcement beyond the ordinary layering every other key
goes through.

### Merge behavior: named-list concatenate

`hooks`/`events` use a merge variant distinct from `SessionConfig`'s scalar/list/key-by-key
variants (docs/specs/process-and-session-config.md's "cross-layer merge behaviors"): for each
hook/event name present in a layer's `hooks`/`events` object, that layer's list is appended to
whatever list earlier layers already built for that name —
`klorb.hooks.merge.concatenate_named_handler_lists`. `load_process_config()`
(`klorb/src/klorb/process_config.py`) calls this once per layer while folding `hooks`/`events`
into `concatenated_hooks`/`concatenated_events` accumulators, via `klorb.hooks.merge.parse_handler_list`
for per-entry pydantic validation (an entry that fails to validate is skipped with a
`config_warnings` message, not fatal to the rest of the layer). The final linear order for a
given hook/event name is simply every layer's entries for that name, in layer order, then
authoring order within a layer — deliberately not a documented contract beyond that: a handler
should not depend on its exact position relative to another layer's handler.

### Config schema

* `HookConfig` (`klorb.hooks.config`): `type` (`"bash"|"classifier"|"chat"`), `shell`/`command`/
  `prompt` (handler-type-specific payload), `name` (optional, disambiguates entries in output/logs),
  `filter` (optional `HookConfigFilter`).
* `HookConfigFilter`: `matches`/`pattern`/`contains`/`any`/`all`/`not` (aliased from `not_` for the
  reserved keyword) — see `klorb.hooks.filters.evaluate_filter`.
* `EventConfig` and its subclasses `FileSystemModifiedEventConfig` (`watch`),
  `TimerEventConfig` (`interval_minutes`/`cron`), `WorkspaceTrustChangedEventConfig` (no extra
  field) — each carries an `action: HookConfig`. `klorb.hooks.config.EVENT_CONFIG_MODELS` maps
  each `EVENT_NAMES` entry to the subclass its list entries parse as.

## Filters

`klorb.hooks.filters.evaluate_filter(filter_, subject)` is a pure function: `matches` is exact
equality, `pattern` is `re.search`, `contains` is substring, `any`/`all` recurse and combine with
OR/AND, `not_` negates a nested filter. Every field a filter clause sets must hold; a filter with
none set is vacuously eligible.

`klorb.hooks.config.HOOK_FILTER_SUBJECT_FIELDS` maps each hook name to which `HookInput` field its
`filter` is evaluated against: `event` for the process/session start/end hooks, `tool_name` for
`onToolUse`, and `message` for every hook centered on a chunk of conversation text
(`onSubmitUserPrompt`, `onToolResult`, `onSubagentStart`, `onSubagentTurnEnd`, `onAgentTurnEnd`).
An event handler's `action.filter` is evaluated the same way, keyed by the event name.

## Wire schema

`klorb.hooks.wire` defines the JSON shapes a handler is invoked with and must reply with:

* **`HookInput`** — `hook`, `name`, `args` (the firing handler's own `shell`/`command`/`prompt`),
  `workspace_root`, `event`, `message`, `tool_name`, `tool_args`, `role`, `session_id` (the firing
  session's own id; `None` only for `onProcessStart`/`onProcessEnd`, before any session exists),
  `workspace_trusted`/`workspace_just_bootstrapped` (set only for `onSessionStart`).
* **`HookOutput`** — `success` (default `True`), `tool_args`, `permission` (a bare `Verdict`),
  `message`, `interrupt` (default `False`).
* **`EventInput(HookInput)`** — adds `fs_updates: list[FileSystemUpdate] | None`, each a
  `{event: "created"|"deleted"|"modified", path}` pair, populated for a `FileSystemModified`
  firing.

A `bash` handler receives `HookInput`/`EventInput` as JSON on stdin (`model_dump_json(by_alias=True)`)
and must print `HookOutput` JSON to stdout. A `classifier` handler receives the same JSON
prepended to its own configured `prompt`, as the first user message. A `chat` handler receives
nothing — it contributes its `prompt` as `HookOutput.message` directly.

## Dispatch and chaining

`klorb.hooks.dispatcher.HookDispatcher` resolves and runs one hook/event firing's configured
handler chain:

* `dispatch(hook_name, hook_input, session_config=...)` — looks up `ProcessConfig.hooks[hook_name]`.
* `dispatch_event(event_name, entries, event_input, session_config=...)` — runs the `action` of
  each already-selected `entries` (e.g. whichever `FileSystemModifiedEventConfig`s had a change
  fall under their own `watch` path); `HookDispatcher` applies no further selection beyond each
  entry's own `action.filter`.

Both funnel into `_run_chain`, which walks the handler list in order:

1. Skip a handler whose `filter` doesn't match the subject (`HOOK_FILTER_SUBJECT_FIELDS`).
2. Run it (`_run_handler`, dispatching on `type`) and fold a non-`None` result into the running
   aggregate `HookOutput` (`_fold`) and into the next handler's input (`chained_input`'s `message`/
   `tool_args`), so a later handler sees an earlier one's rewrites rather than the chain's original
   input.
3. A handler that returns `None` (see "Error handling" below) contributes nothing — the next
   handler still sees the previous *valid* handler's output, not a synthesized failure.

`_fold(accumulated, latest)`: `success` is `accumulated.success and latest.success` (once any
valid handler says `False`, the aggregate stays `False`); `tool_args`/`message` take `latest`'s
value when set, else carry `accumulated`'s forward; `interrupt` is `True` once any handler asks
for it; `permission` is reduced via `_fold_permission`, which defers to
`klorb.permissions.table.stricter_verdict` once two handlers have both opined (a handler that
leaves `permission` unset contributes no opinion and never pulls the aggregate toward `deny`).

`HookDispatcher.dispatch`/`dispatch_event` never raise — a hook is a policy overlay, not something
that can crash the lifecycle moment it's attached to.

## Handler types

### `bash` (`klorb.hooks.bash_handler.run_bash_handler`)

Runs a one-off subprocess sandboxed the same way `BashTool` sandboxes an agent-issued command:
`klorb.sandbox.build_bwrap_argv()`, the same function `BashTool._bwrap_prefix`
(`klorb/src/klorb/tools/bash.py`) calls — not a second sandboxing path. Falls back to running
unsandboxed (logged at `debug`) when `bwrap_available()` is `False`.

* `shell` runs as `[bash_command, "-c", handler.shell]`; `command` elements are macro-expanded
  (`${home}`/`${workspaceRoot}`, via `klorb.config_macros.expand_macros`) then run as-is —
  `shell` gets no macro expansion.
* The subprocess env is `HOME`/`USER` (shared from the klorb process), `WORKSPACE_ROOT`, then
  `SessionConfig.share_env`/`set_env`'s passthrough (same precedence
  `klorb.tools.bash.build_bash_env` uses), then `KLORB_HOOK_ENV_FILE`
  (`klorb.hooks.bash_handler.HOOK_ENV_FILE_VAR`) pointing at a fresh, empty, per-invocation file
  under `KLORB_STATE_DIR / "hooks"`, granted `writeFiles` access in the sandbox and deleted once
  the subprocess exits. It exists so a hook script has a place to read/write values without them
  becoming a tool-call argument visible to the model; nothing populates it with content today.
* Bounded by `timeout_seconds` — `ProcessConfig.hook_bash_timeout_seconds`
  (`hooks.bash.timeoutSeconds`) if set, else `ProcessConfig.bash_timeout_seconds`
  (`tools.bash.timeout`)'s own value.
* Returns `None` (never raises) for: neither `shell` nor `command` set, a malformed `command`
  macro reference, a launch `OSError`, a timeout, a non-zero exit, or stdout that doesn't parse as
  `HookOutput` JSON — each case logged at `warning`.

### `classifier` (`klorb.hooks.classifier_handler.run_classifier_handler`)

A sibling implementation of `klorb.session_naming.generate_session_name`'s pattern (same shape,
not a shared call): its own system prompt framing the incoming `HookInput` JSON as untrusted
content rather than instructions, a strict-JSON `HookOutput`-shaped `response_format`, one
parse-retry, and an `e2e_timeout` deadline (`threading.Timer` setting a `cancel_event`) wrapping
the whole call including the retry. Never raises — any failure (request error, timeout, a reply
that still fails to validate after retry) returns `None`.

Model resolution (`HookDispatcher._resolve_classifier_model`): `ProcessConfig.session_classifier_model`
if set, else a model registered under `NANO_CLASSIFIER_CAPABILITY`
(`klorb.session_naming.NANO_CLASSIFIER_CAPABILITY`) if a `ModelRegistry` was supplied, else
`DEFAULT_SESSION_CLASSIFIER_MODEL` — the same fallback order session naming uses for this same
classifier slot. `classifier.model`/`classifier.timeout`/`classifier.e2eTimeout` config keys are
reused rather than duplicated. A `classifier` handler configured for a hook firing with no
`ApiProvider` available yet (`onProcessStart`, before `klorb.cli.main()` constructs one)
contributes nothing, logged at `warning`.

### `chat`

No subprocess, no model call: `HookDispatcher._run_handler` returns `HookOutput(message=handler.prompt)`
directly (or `None`, logged at `warning`, if `prompt` is unset). The caller — a turn/tool hook
point, or event delivery — decides what to do with the aggregate `message`; see "Chained turns"
below.

## Chained turns: `Session.start_turn_or_enqueue`

`Session.start_turn_or_enqueue(text)` (`klorb/src/klorb/session/mixins/turns.py`) is the one piece
of library plumbing a `chat` handler's message needs beyond `Session.send_turn()`
(already the shared "start a turn" primitive both the TUI and ACP server call directly): deciding
whether to call `send_turn()` fresh or `Session.enqueue_queued_message()` instead, based on
whether a turn is already in flight. The TUI and ACP server never need this decision in library
code — a user at a textarea, or the JSON-RPC layer rejecting a concurrent `session/prompt`,
already knows the answer externally. A hook/event dispatcher has no such external signal, so it
needs the check-and-branch itself.

Bounded by `SessionConfig.max_chained_hook_turns` (`tools.hooks.maxChainedTurns`, default
`DEFAULT_MAX_CHAINED_HOOK_TURNS = 5`, `klorb/src/klorb/session/constants.py`): `Session._chained_hook_turns`
counts consecutive turns `start_turn_or_enqueue` has itself started, and is reset to `0` by any
ordinary user- or tool-driven turn. Once the cap is reached, `start_turn_or_enqueue` refuses to
start (or queue) another turn, logged at `warning`, until the counter resets — the same fail-safe
shape as `max_tool_calls_per_turn`/`max_tool_calls_per_session`
(docs/specs/process-and-session-config.md).

## Available hooks

`Session._dispatch_hook` (`klorb/src/klorb/session/mixins/core.py`) is the shared building block
every hook-firing call site funnels through: builds a `HookInput` tagged with the firing session's
`workspace`/`role`/`id`, and dispatches it via `HookDispatcher`. Returns a default (`success=True`,
no message) `HookOutput` for a `Session` constructed without a `ProcessConfig` (most unit tests) —
hooks are inert in that case rather than erroring.

| Hook | Fires from | Scope |
| --- | --- | --- |
| `onProcessStart` | `klorb.cli.main()`, before workspace/session setup | process |
| `onProcessEnd` | `klorb.cli.main()`, at exit | process |
| `onSessionStart` | `Session.fire_session_start_hook`; for the TUI, called from `_resolve_workspace_trust()` (`klorb/src/klorb/tui/mixins/workspace_bootstrap.py`) once trust is settled; for headless/ACP, at construction, since trust is already final there | root session |
| `onSessionEnd` | `Session.fire_session_start_hook`'s counterpart at session close, `event="DestroySession"` | root session |
| `onSubmitUserPrompt` | `_apply_submit_user_prompt_hook` (`klorb/src/klorb/session/mixins/turns.py`), before a turn's message reaches the model; a `success=False` aggregate raises `HookDeniedTurnError`, blocking the turn | root session |
| `onAgentTurnEnd` | `_fire_agent_turn_end_hook`, after the agent's final message; a `message` in the aggregate result is passed to `start_turn_or_enqueue` | root session |
| `onToolUse` | `_apply_tool_use_hook` (`klorb/src/klorb/session/mixins/tool_execution.py`), before a tool call runs; `tool_args` in the result replaces the call's args, `success=False` or a `permission` of `"deny"`/`"ask"` blocks the call | whole tree |
| `onToolResult` | `_apply_tool_result_hook`, after a tool call's result is available; `message` in the result replaces the result content | whole tree |
| `onSubagentStart` | `Session.fire_subagent_start_hook`, called from `klorb.agents.policy` around a subagent's turn; a `None` return (aggregate `success=False`) skips the turn entirely | firing subagent |
| `onSubagentTurnEnd` | `Session.fire_subagent_turn_end_hook`, after a subagent's turn | firing subagent |

`onRequestPermission` is named in `klorb.hooks.config.HOOK_NAMES` but not yet wired to any call
site — see "Out of scope" below.

`onToolUse` has no interactive channel to a human today: a `permission` verdict of `"ask"` is
treated the same as `"deny"`, an unconditional veto, since wiring `"ask"` through to a live
permission prompt is `onRequestPermission`'s own deferred design.

### Scope across the subagent tree

Subagents (docs/specs/subagents.md) are separate `Session` objects nested under a root session.
`onToolUse`/`onToolResult` fire identically for any session in the tree, tagged via `HookInput.role`/
`session_id` with whichever session fired them, so a `filter` can single out subagent activity.
Every other hook above is scoped to exactly one side: `onSessionStart`/`onSessionEnd`/
`onSubmitUserPrompt`/`onAgentTurnEnd` never fire for a subagent's own turns, and
`onSubagentStart`/`onSubagentTurnEnd` never fire for the root session. `onProcessStart`/
`onProcessEnd` have no session at all yet/anymore when they fire.

## Available events

An event's aggregate `HookOutput.message` is delivered via `Session.deliver_event_message`
(`klorb/src/klorb/session/mixins/turns.py`): if a turn is already running, `text` is queued
verbatim through `start_turn_or_enqueue` as an interjection; otherwise a fresh turn starts, `text`
prefixed with `"An event has resumed this conversation:\n"` to make clear what woke the
conversation back up on its own. Both `FileSystemModified` and `WorkspaceTrustChanged` always
target the root session's conversation, never a subagent's, regardless of which session happens to
be active when they fire.

### `FileSystemModified` (`klorb.hooks.fs_events.FileSystemWatcher`)

Built on `watchdog.observers.Observer`/`FileSystemEventHandler`, following the same debounce
pattern (`threading.Timer`-based flush) `klorb.tui.workspace_file_index.WorkspaceFileIndex` uses
for the `@`-mention file index, generalized to a configurable window
(`MIN_EVENT_DEBOUNCE_SECONDS = 10.0`, `klorb.hooks.config`, shared with `Timer`'s own floor).
Watches each configured entry's `watch` target (a directory watched recursively; a file's parent
directory, since inotify can't watch a single file directly); directory-level create/delete events
are dropped (a directory's own creation/deletion carries no path a `watch` entry's `action` would
meaningfully act on). After a debounce window settles, `_flush` matches the batch's deduplicated
`(event, path)` updates against each entry's own `watch` path and calls `dispatch` once with
whichever entries matched and an `EventInput` batch of the matched updates — an update outside
every configured `watch` is dropped rather than delivered. Started at root-session start (once the
workspace is resolved), torn down at root-session end.

### `Timer` (`klorb.hooks.timer_events.TimerScheduler`)

**Best-effort only, not real cron.** klorb has no persistent daemon mode
(docs/specs/klorb-server.md: `klorb server` exits the moment its one client disconnects) — a
`Timer` entry only fires while some klorb process for the workspace already happens to be running
for other reasons; a fire time that elapses while nothing is running is simply missed, never
queued or caught up on restart.

Each entry gets its own self-rescheduling `threading.Timer`, computed by `compute_next_fire`
(a pure function, unit-testable without a real clock): `after + interval_minutes` for an interval
entry, or `croniter(entry.cron, after).get_next(datetime)` for a cron entry. `clamp_timer_intervals`
enforces the "no more frequent than once every 10 seconds" floor at config-load time: an
`interval_minutes` tighter than `MIN_EVENT_DEBOUNCE_SECONDS / 60` is clamped up to it, with a
`config_warnings` entry — idempotent, since `load_process_config` re-applies it across every layer.
A `cron` entry needs no such clamp: standard five-field cron grammar can't express anything
tighter than once a minute, already above the floor. An entry with an invalid `cron` string (or
neither `cron` nor `interval_minutes` set) is simply not scheduled, logged at `warning`, rather
than crashing the scheduler thread.

### `WorkspaceTrustChanged`

Fires via `Session.fire_workspace_trust_changed_hook(event)` at the two points a workspace's trust
decision can change against an already-live root session: the TUI's `>Trust workspace` command
(`event="TrustCommand"`, `klorb/src/klorb/tui/mixins/workspace_bootstrap.py`) and ACP's
`_klorb/trustWorkspace` (`event="AcpTrustWorkspace"`, `klorb.server.klorb_agent.KlorbAcpAgent`). Needs
no watcher/scheduler of its own — it dispatches directly through the same `HookDispatcher`/
`start_turn_or_enqueue` path the other two events use. Distinct from `onSessionStart`'s own
`workspace_trusted`/`workspace_just_bootstrapped` fields, which report a session's *initial* trust
state as a one-time, planned fact at startup, not a later, unplanned change.

## Error handling

A handler that times out, exits non-zero (`bash`), or fails to produce valid `HookOutput` JSON
contributes nothing to the chain — the next handler, if any, sees the previous *valid* handler's
output, not a synthesized failure. Logged at `warning`, never raised out to the lifecycle moment
the hook was attached to.

A malformed hook/event config entry (unknown `type`, a `raw_handlers` value that isn't a list, an
entry that fails pydantic validation) is caught at config-load time
(`klorb.hooks.merge.parse_handler_list`) and collected into `ProcessConfig.config_warnings` — the
same place any other unrecognized/invalid on-disk key already goes
(docs/specs/process-and-session-config.md) — rather than failing silently or crashing process
startup.

## Out of scope

* **`onRequestPermission` hook.** `HOOK_NAMES` reserves the name and
  `HOOK_FILTER_SUBJECT_FIELDS` gives it a placeholder subject field, but no call site dispatches
  it. A real design needs to reconcile `HookOutput.permission` (a bare `Verdict`) against the
  richer `PermissionDecision` (`action`+`scope`, `klorb/src/klorb/session/events.py`) a human/UI
  answer produces.
* **A genuine persistent daemon mode**, so `Timer` can become real scheduling instead of
  best-effort.
* **Hot-reloading hook/event config** edited mid-process, without a full restart.
* **Surfacing hook activity in the UI** — a TUI/VSCode view of which hooks fired, what they
  returned, and whether they errored, beyond `logger.debug()`/`warning()` output.
* **Real content for `KLORB_HOOK_ENV_FILE`.** A `bash` handler's subprocess is pointed at a fresh,
  empty file per invocation; nothing writes session-scoped values into it yet, and ordinary
  `BashTool` commands don't share it.
* **An explicit turn-interrupt primitive** hooks/events can call directly, rather than
  `HookOutput.interrupt` needing new wiring on top of a turn's `cancel_event`
  (`klorb.session.events.TurnEventHandlers`) each time a caller wants it.
