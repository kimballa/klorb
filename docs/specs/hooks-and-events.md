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
`filter` is evaluated against: `reason` for the process/session start/end hooks, `tool_name` for
`onToolUse`, `tool_result` for `onToolResult`, `skill_name` for `onActivateSkill`, and `message`
for every hook centered on a chunk of conversation text (`onSubmitUserPrompt`, `onSubagentStart`,
`onSubagentTurnEnd`, `onAgentTurnEnd`). An event handler's `action.filter` is evaluated the same
way, keyed by the event name.

## Hook API schema

`klorb.hooks.hook_api` defines the JSON shapes a handler is invoked with and must reply with:

* **`HookInput`** — `hook`, `name`, `args` (the firing handler's own `shell`/`command`/`prompt`),
  `workspace_root`, `reason` (why `hook` fired — an event's own name is carried in `hook` itself,
  never here), `message`, `tool_name`, `tool_args`, `tool_result` (`onToolResult` only — the
  call's own substantive result content, never `system_interjections`/`user_interjections`),
  `skill_name`/`skill_namespace`/`is_user_mentioned`/`is_user_activated` (set only for
  `onActivateSkill`), `role`, `session_id`/`root_session_id` (the firing session's own id and the
  root session it descends from; `None` only for `onProcessStart`/`onProcessEnd`, before any
  session exists), `exit_status` (set only for `onProcessEnd`, read-only),
  `workspace_trusted`/`workspace_just_bootstrapped` (set only for `onSessionStart`).
* **`HookOutput`** — `success` (default `True`), `tool_args`, `permission` (a bare `Verdict`),
  `message`, `tool_result` (`onToolResult` only — replaces `response_body`/`error_message` in the
  envelope), `interrupt` (default `False`), `reset_session` (default `False`).
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
value when set, else carry `accumulated`'s forward; `interrupt`/`reset_session` are `True` once
any handler asks for them; `permission` is reduced via `_fold_permission`, which defers to
`klorb.permissions.table.stricter_verdict` once two handlers have both opined (a handler that
leaves `permission` unset contributes no opinion and never pulls the aggregate toward `deny`).

Once the whole chain has folded, `_run_chain` enforces `reset_session`'s one invariant: it's valid
only alongside a non-empty `message`. An aggregate that sets `reset_session` without one has it
reset to `False`, logged at `warning` — the same "handler contributed something invalid, drop it"
shape "Error handling" below uses elsewhere, just applied to the final aggregate rather than one
handler's raw output, since `message` can come from an earlier handler in the chain than the one
that set `reset_session`.

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
  `klorb.tools.bash.build_bash_env` uses), then `KLORB_ENV_FILE`
  (`klorb.tools.bash.KLORB_ENV_FILE_VAR`) pointing at the firing session's `session_env_file()` --
  created empty on first use, kept for the rest of the session (not per-invocation), granted
  `writeFiles` access in the sandbox. Unset when no live session exists yet (`onProcessStart`/
  `onProcessEnd`). It exists so a hook script has a place to read/write values without them
  becoming a tool-call argument visible to the model; nothing populates it with content today,
  and an ordinary model-issued `Bash` command doesn't see it yet.
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

## Session reset: `reset_session`

`HookOutput.reset_session` wipes the firing session's conversation and starts it over *in place*
— same `id`, same on-disk `sessions/<subdir>/` directory — seeded with `message` as its next turn.
Unlike the object-replacement design this superseded (see
docs/adrs/00182-reset-session-mutates-the-session-in-place-instead-of-replacing-it.md), no host
(TUI/ACP/headless) needs to participate: `Session` mutates its own fields and never needs to hand
a new object back to whatever holds it.

Only two call sites read it — `SessionTurnsMixin._fire_agent_turn_end_hook`
(`klorb/src/klorb/session/mixins/turns.py`) for `onAgentTurnEnd`, and `SessionCoreMixin.close()`
(`klorb/src/klorb/session/mixins/core.py`) for `onSessionEnd` — every other hook/event can set the
field (`HookDispatcher` doesn't know which hook fired when it folds/validates the aggregate), but
nothing consumes it there.

`Session.reset_session(message)` (`klorb/src/klorb/session/mixins/core.py`) is the shared
mechanism both call sites invoke:

1. Cascade-closes any live subagents (`klorb.agents.runtime.cascade_close_subagents`) — their
   relayed output lands in `self._messages` only to be wiped a moment later, same as a real
   `close()` would capture it first.
2. Reinitializes `config` from `ProcessConfig.session`'s template (a `model_copy()`, with the same
   `PermissionFrameworkState` deep-copy `__init__` does for a root session), then rebuilds `_role`/
   `_system_prompt` from it — a no-op without a `ProcessConfig` (most unit tests).
3. Calls `_reset_state()` — the same method `__init__` itself calls, so construction and reset can
   never drift apart. Resets every conversation-scoped field to a freshly constructed `Session`'s
   own value: `_messages`, the one-shot interjection-seeded flags, `_chained_hook_turns`,
   `_tool_calls_this_session`/`_tool_calls_this_turn`, `_queued_messages`,
   `_standing_interjection_providers`, `statistics`, `subagent_tracker`, `tool_state`, and more.
   Tears down and recreates every conversation-scoped `register_teardown` resource (`Scratchpad`,
   `Bash`'s persistent shell if one is live) — everything except the workspace/process-level
   `FileSystemWatcher`/`TimerScheduler` teardowns a root session's event watchers register once at
   start and never recreate mid-session (`_INFRASTRUCTURE_TEARDOWN_SUBJECTS`). Deliberately leaves
   alone: identity (`id`/`root_id`/`parent`/`depth`/`aliases`) and persistence identity
   (`_session_lock`/`_session_subdir`/`_session_claimed`) — a reset keeps using the same session,
   the same on-disk directory, not a new one.
4. Sets `_session_name = None`/`_session_naming_pending = True`, so the session-naming classifier
   re-runs against the reset conversation's own first turn.
5. Calls `start_turn_or_enqueue(message)` — the same delivery a `chat` handler's message gets.

No deferred/flag-based scheduling is needed: `reset_session()` runs synchronously, on whichever
thread already called it. For `onAgentTurnEnd`, that's `_dispatch_turn` after `_send_and_receive`'s
own local variables (the completed turn's `result_text`) have already been captured — nothing on
that call stack still depends on `self._messages` reflecting the just-finished turn once the reset
runs. `send_turn()` itself does nothing with `self._messages` after `_dispatch_turn()` returns.

`close()`'s own `onSessionEnd` dispatch aborts the shutdown entirely when the result sets
`reset_session`: it returns immediately after calling `reset_session()`, running none of
cascade-closing subagents, `_finalize_session_persistence()`, or its own outer teardown-callback
loop — this session isn't actually ending, so none of that applies; `reset_session()`'s own,
narrower cleanup (step 3 above) runs instead.

**`onSessionEnd` also fires for an `onAgentTurnEnd`-triggered reset.** Before calling
`reset_session()`, `_fire_agent_turn_end_hook` dispatches `onSessionEnd` with
`reason="ResetSession"` — purely for side effects (e.g. a bash handler logging that this
conversation ended); its own `HookOutput` is discarded, the same way every other `onSessionEnd`
firing is non-cancelable. `close()`'s own `onSessionEnd` firing (a real `SuspendSession`) doesn't
need this extra dispatch, since `onSessionEnd` is already what fired there.

Works identically in the TUI, ACP, and headless — there is no host-specific wiring left to
differ.

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
| `onSessionEnd` | `Session.fire_session_start_hook`'s counterpart at session close, `reason="SuspendSession"` (or `"ResetSession"`, dispatched by `onAgentTurnEnd`'s own reset handling); a `reset_session` result calls `Session.reset_session()` instead of continuing shutdown (see "Session reset" above) | root session |
| `onSubmitUserPrompt` | `_apply_submit_user_prompt_hook` (`klorb/src/klorb/session/mixins/turns.py`), before a turn's message reaches the model; a `success=False` aggregate raises `HookDeniedTurnError`, blocking the turn | root session |
| `onAgentTurnEnd` | `_fire_agent_turn_end_hook`, after the agent's final message; a `message` in the aggregate result is passed to `start_turn_or_enqueue`, unless `reset_session` is also set (see "Session reset" above) | root session |
| `onToolUse` | `_apply_tool_use_hook` (`klorb/src/klorb/session/mixins/tool_execution.py`), before a tool call runs; `tool_args` in the result replaces the call's args, `success=False` or a `permission` of `"deny"`/`"ask"` blocks the call | whole tree |
| `onToolResult` | `_apply_tool_result_hook`, after a tool call's result envelope is built; `tool_result` in the result replaces `response_body`/`error_message` in the envelope, never `system_interjections`/`user_interjections` | whole tree |
| `onActivateSkill` | `Session.fire_activate_skill_hook` (`klorb/src/klorb/session/mixins/skills.py`), from `ActivateSkillTool.apply()` and from `_build_user_skill_activation_interjection`'s leading-mention fast path, once `skillRules` has already let the activation through; `success=False` or a `permission` of `"deny"`/`"ask"` vetoes it — `ActivateSkillTool.apply()` raises `ToolCallError`, the leading-mention path falls back to the ordinary `SkillReference` reminder | whole tree |
| `onSubagentStart` | `Session.fire_subagent_start_hook`, called from `klorb.agents.policy` around a subagent's turn; a `None` return (aggregate `success=False`) skips the turn entirely | firing subagent |
| `onSubagentTurnEnd` | `Session.fire_subagent_turn_end_hook`, after a subagent's turn | firing subagent |

`onRequestPermission` is named in `klorb.hooks.config.HOOK_NAMES` but not yet wired to any call
site — see "Out of scope" below.

`onToolUse` has no interactive channel to a human today: a `permission` verdict of `"ask"` is
treated the same as `"deny"`, an unconditional veto, since wiring `"ask"` through to a live
permission prompt is `onRequestPermission`'s own deferred design.

### Scope across the subagent tree

Subagents (docs/specs/subagents.md) are separate `Session` objects nested under a root session.
`onToolUse`/`onToolResult`/`onActivateSkill` fire identically for any session in the tree, tagged
via `HookInput.role`/`session_id` with whichever session fired them, so a `filter` can single out
subagent activity. Every other hook above is scoped to exactly one side: `onSessionStart`/`onSessionEnd`/
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

Fires via `Session.fire_workspace_trust_changed_hook(reason)` at the two points a workspace's trust
decision can change against an already-live root session: the TUI's `>Trust workspace` command
(`reason="TrustCommand"`, `klorb/src/klorb/tui/mixins/workspace_bootstrap.py`) and ACP's
`_klorb/trustWorkspace` (`reason="AcpTrustWorkspace"`, `klorb.server.klorb_agent.KlorbAcpAgent`). Needs
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
* **Real content for `KLORB_ENV_FILE`.** A `bash` handler's subprocess is pointed at a
  session-scoped, otherwise-empty file; nothing writes values into it yet, and ordinary
  `BashTool` commands don't share it.
* **An explicit turn-interrupt primitive** hooks/events can call directly, rather than
  `HookOutput.interrupt` needing new wiring on top of a turn's `cancel_event`
  (`klorb.session.events.TurnEventHandlers`) each time a caller wants it.
