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
  envelope), `interrupt` (default `False`), `reset_session` (default `False`), `log` (a debugging
  note — see "Debugging: `HookOutput.log`" below).
* **`EventInput(HookInput)`** — adds `fs_updates: list[FileSystemUpdate] | None`, each a
  `{event: "created"|"deleted"|"modified", path}` pair, populated for a `FileSystemModified`
  firing, and `is_agent_active: bool | None`, whether the root session's agent is mid-turn at the
  moment the event fires — set for every event.

A `bash` handler receives `HookInput`/`EventInput` as JSON on stdin (`model_dump_json()`) and must
print `HookOutput` JSON to stdout. A `classifier` handler receives the same JSON prepended to its
own configured `prompt`, as the first user message. A `chat` handler receives nothing — it
contributes its `prompt` as `HookOutput.message` directly.

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
  populated on first use with `NO_COLOR`, `share_env`, and `set_env` exports (single-quoted),
  kept for the rest of the session (not per-invocation), granted `writeFiles` access in the
  sandbox. Unset when no live session exists yet (`onProcessStart`/`onProcessEnd`). It exists so
  a hook script has a place to read/write values without them becoming a tool-call argument
  visible to the model.
* Bounded by `timeout_seconds` — `ProcessConfig.hook_bash_timeout_seconds`
  (`hooks.bash.timeoutSeconds`) if set, else `ProcessConfig.bash_timeout_seconds`
  (`tools.bash.timeout`)'s own value.
* Returns `None` (never raises) for: neither `shell` nor `command` set, a malformed `command`
  macro reference, a launch `OSError`, a timeout, a non-zero exit, or stdout that doesn't parse as
  `HookOutput` JSON — each case logged at `warning`.
* Whenever the subprocess writes anything to stderr — regardless of exit code — it's logged at
  `warning` verbatim, prefixed with which hook and handler produced it. A handler that writes
  nothing to stderr logs nothing for this.

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

## Chained turns: delivery through the queued-message drain loop

`onAgentTurnEnd`/`onSubagentTurnEnd`'s `chat`-handler message is meant to auto-continue the
conversation. `Session._deliver_chained_hook_message(message)`
(`klorb/src/klorb/session/mixins/core.py`) is what a firing hook calls with it — but it does not
dispatch a turn itself. It enqueues `message` onto the same `_queued_messages` queue a user's
typed-ahead message already uses (`Session.enqueue_queued_message`, tagged
`QueuedMessage(origin="chained_hook")`), to be picked up by whichever drain-and-resubmit loop is
already driving this session once the current turn ends: TUI's `_finish_turn`
(`klorb/src/klorb/tui/mixins/prompt_submission.py`, which resubmits through `_submit_prompt`, the
literal Enter-key front door), ACP's `TurnBridge.run_turn`
(`klorb/src/klorb/server/turn_bridge.py`, which already loops calling `send_turn()` again for
anything left queued), `Session.run_one_shot`, and `klorb.agents.policy._run_subagent_turn`. All
four call `Session.drain_next_turn_text()` (or, for ACP's case, `drain_queued_messages()` plus
`mark_next_turn_continuation()` directly, since it also needs the raw per-message list for its own
`_klorb/queuedMessageSent` notifications) at the point they'd otherwise be done. This is why a
chained turn renders exactly like an ordinary submission with no special-cased UI: it's the exact
same resubmission path a user typing ahead during a turn already uses, and nothing is enqueued
until the current turn has fully ended, so there is no pending/italic phase to render either.

`onAgentTurnEnd`/`onSubagentTurnEnd` fire synchronously on the same call stack as whichever host's
`send_turn()` call is still waiting on this turn — so a live host is always present for this path.
Event delivery and `close()`'s `onSessionEnd`-triggered reset don't share that guarantee; see
"Session reset" and "Available events" below.

Bounded by `SessionConfig.max_chained_hook_turns` (`tools.hooks.maxChainedTurns`, default
`DEFAULT_MAX_CHAINED_HOOK_TURNS = 5`, `klorb/src/klorb/session/constants.py`; `0` disables
chaining, negative means unlimited): `Session._chained_hook_turns` counts consecutive turns
`_deliver_chained_hook_message` has chained. Once the cap is reached, it refuses to enqueue
another one, logged at `warning` — the same fail-safe shape as `max_tool_calls_per_turn`
(docs/specs/process-and-session-config.md).

Chained turns are now independent, decoupled `_dispatch_turn()` calls rather than nested
recursive stack frames, so the counter can't reset itself by unwinding. Instead,
`_dispatch_turn()` resets it to `0` unconditionally at the start of every turn — the one place
every turn (root, subagent, retry, or a chained continuation) funnels through, so an ordinary
turn, a `retry_last_turn`, and whatever happens after an aborted or errored turn (none of which
ever reach `_fire_agent_turn_end_hook`, since that's only called on a clean completion) all reset
it by default. The only exception is a one-shot flag, `Session._chain_continuation_pending`, that
`drain_next_turn_text()`/`mark_next_turn_continuation()` sets immediately before a host resubmits
a drained batch, but only when every message in it originated from a chat handler's own chaining
— a real user or event message mixed into the same batch resets the counter like any ordinary
turn.

## Session reset: `reset_session`

`HookOutput.reset_session` wipes the firing session's conversation and starts it over *in place*
— same `id`, same on-disk `sessions/<subdir>/` directory — seeded with `message` as its next turn.
Unlike the object-replacement design this superseded (see
docs/adrs/00182-reset-session-mutates-the-session-in-place-instead-of-replacing-it.md), no host
(TUI/ACP/headless) needs to participate: `Session` mutates its own fields and never needs to hand
a new object back to whatever holds it.

Three call sites read it — `SessionTurnsMixin._fire_agent_turn_end_hook`
(`klorb/src/klorb/session/mixins/turns.py`) for `onAgentTurnEnd`, and `SessionCoreMixin.
_dispatch_event_entries`/`fire_workspace_trust_changed_hook` (`klorb/src/klorb/session/mixins/
core.py`, via the shared `_deliver_or_reset_event` helper) for `Timer`/`FileSystemModified`/
`WorkspaceTrustChanged`. `onSessionEnd` never does: `HookDispatcher._run_chain` drops
`reset_session` from that hook's aggregate result unconditionally (see "`reset_session` is
opt-in per hook/event name" below), so `close()` never sees it set.

`Session.reset_session()` (`klorb/src/klorb/session/mixins/core.py`) is the shared mechanism
both call sites invoke. It does not deliver `message` itself — the two callers have different
hosts (or none) available and each delivers it separately, right after calling this:

1. Cascade-closes any live subagents (`klorb.agents.runtime.cascade_close_subagents`) — their
   relayed output lands in `self._messages` only to be wiped a moment later, same as a real
   `close()` would capture it first.
2. Reinitializes `config` from `ProcessConfig.session`'s template (a `model_copy()`, with the same
   `PermissionFrameworkState` deep-copy `__init__` does for a root session), then rebuilds `_role`/
   `_system_prompt` from it — a no-op without a `ProcessConfig` (most unit tests).
3. Calls `_reset_state()` — the same method `__init__` itself calls, so construction and reset can
   never drift apart. Resets every conversation-scoped field to a freshly constructed `Session`'s
   own value: `_messages`, the one-shot interjection-seeded flags, `_chained_hook_turns`,
   `_tool_calls_this_turn`, `_queued_messages`,
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

No deferred/flag-based scheduling is needed: `reset_session()` runs synchronously, on whichever
thread already called it. For `onAgentTurnEnd`, that's `_dispatch_turn` after `_send_and_receive`'s
own local variables (the completed turn's `result_text`) have already been captured — nothing on
that call stack still depends on `self._messages` reflecting the just-finished turn once the reset
runs. `send_turn()` itself does nothing with `self._messages` after `_dispatch_turn()` returns.

Each caller delivers `message` the same way it delivers any other continuation:

* **`_fire_agent_turn_end_hook`'s reset branch** is still on the same call stack as whichever
  host's `send_turn()` call is waiting on this turn (see "Chained turns" above) — it calls
  `reset_session()` then `_deliver_chained_hook_message(message)` directly, exactly like an
  ordinary chained continuation, so the reset conversation's first turn renders normally.
* **`_dispatch_event_entries`/`fire_workspace_trust_changed_hook`'s reset branch**
  (`_deliver_or_reset_event`) calls `reset_session()` then `deliver_event_message(message)` —
  the same idle-or-turn-in-flight delivery an ordinary event message uses (see "Available
  events" below), including waking a registered host when idle.

**`onSessionEnd` also fires for an `onAgentTurnEnd`-triggered reset.** Before calling
`reset_session()`, `_fire_agent_turn_end_hook` dispatches `onSessionEnd` with
`reason="ResetSession"` — purely for side effects (e.g. a bash handler logging that this
conversation ended); its own `HookOutput` is discarded, the same way every `onSessionEnd` firing
is non-cancelable (see below).

### `reset_session` is opt-in per hook/event name

`HookDispatcher._run_chain` only lets `reset_session` survive folding for names in
`klorb.hooks.config.RESET_SESSION_CAPABLE_HOOKS` (`onAgentTurnEnd`, `Timer`,
`FileSystemModified`, `WorkspaceTrustChanged`) — every other name's aggregate has it dropped
silently, the same way `tool_result` is ignored outside `onToolResult`. `onSessionEnd` is
notably not in that set: `close()` never branches on the result at all, dispatching
`onSessionEnd` for handler side effects and `log` only, and shutdown always proceeds. This is
deliberate, not a gap pending a fix — a host that's already decided this exact session is going
away has no "come drain the queue" wake-up available to it. See
docs/adrs/00187-session-register-wake-handler-tells-an-idle-host-to-drain-and-resubmit.md.

## Debugging: `HookOutput.log`

`HookOutput.log` is a handler's own debugging note — distinct from `message`, which the
dispatcher chains into the conversation. `HookDispatcher._run_chain` logs each handler's
non-`None` `log` at `info`, before folding: `Hook <hook-name> handler '<name>': <log-string>`.
It's folded across a chain the same way `message` is (the latest handler to set it wins).

Separately, `Session.deliver_notice(text)` surfaces the aggregate's `log` verbatim (no `Hook ...`
prefix) to whichever UI is attached to the firing session, via a callback registered with
`Session.register_notice_handler()` — a no-op if none is registered (most unit tests). Every
call site that dispatches a hook/event (`_dispatch_hook`, `_dispatch_event_entries`,
`fire_workspace_trust_changed_hook`, all in `klorb/src/klorb/session/mixins/core.py`) calls
`deliver_notice()` when the result carries a `log`.

Each host registers its own handler once a `Session` exists, replacing any previous
registration on session replacement (`/clear`, `session/new`, `session/load`):

* **TUI** (`klorb.tui.ReplApp._wire_session_notice_handler`) posts a `TuiHistoryNotice` — the
  same thread-safe hand-off `TuiHistoryLogHandler` uses (see docs/specs/paths-and-logging.md) —
  mounted into the history scroll as a neutral (non-error) notice.
* **ACP** (`KlorbAcpAgent._wire_session_notice_handler`) sends a `_klorb/notice` extension
  notification (see docs/specs/klorb-server.md's "Extension methods" section), hopping onto the
  agent's event loop via `asyncio.run_coroutine_threadsafe` since the firing hook may run on a
  background `FileSystemModified`/`Timer` watcher thread. The VS Code webview renders it as a
  `'notice'`-kind history entry, the same rendering an interrupted/aborted-turn notice uses.
* **Headless** (`klorb.cli.main()`) registers `print` directly on the one-shot `Session`.
  `onProcessStart`/`onProcessEnd` fire before any `Session` exists, so `main()` prints their
  `HookOutput.log` directly instead.

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
| `onSessionEnd` | `Session.fire_session_start_hook`'s counterpart at session close, `reason="SuspendSession"` (or `"ResetSession"`, dispatched by `onAgentTurnEnd`'s own reset handling); its result never initiates a reset (see "`reset_session` is opt-in per hook/event name" above) | root session |
| `onSubmitUserPrompt` | `_apply_submit_user_prompt_hook` (`klorb/src/klorb/session/mixins/turns.py`), before a turn's message reaches the model; a `success=False` aggregate raises `HookDeniedTurnError`, blocking the turn | root session |
| `onAgentTurnEnd` | `_fire_agent_turn_end_hook`, after the agent's final message; a `message` in the aggregate result is passed to `_deliver_chained_hook_message`, unless `reset_session` is also set (see "Session reset" above) | root session |
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

An event's aggregate `HookOutput.message`/`reset_session` is delivered via
`_deliver_or_reset_event` → `Session.deliver_event_message` (`klorb/src/klorb/session/mixins/
turns.py`): if a turn is already running, `text` is queued (`QueuedMessage(origin="event")`, so
`drain_next_turn_text` resets `_chained_hook_turns` for it like any non-chained message) for
that turn's own host to pick up once it ends, the same live-host mechanism ordinary hook
chaining relies on (see "Chained turns" above). Otherwise there is no turn in flight: `text` is
still queued, then whichever host registered a wake handler (`Session.register_wake_handler`,
see docs/adrs/00187-session-register-wake-handler-tells-an-idle-host-to-drain-and-resubmit.md)
is pinged to drain and resubmit it through its own front door. Only with no registered handler
at all (a subagent, a `Session` built for a unit test, or headless outside its own
`run_one_shot()` loop) does it raise `ChainedHookMessageUndeliverableError` rather than
dispatching invisibly. Both `FileSystemModified` and `WorkspaceTrustChanged` always target the
root session's conversation, never a subagent's, regardless of which session happens to be
active when they fire. Every event sets `EventInput.is_agent_active` to whether that root
session's `current_turn_handlers()` is non-`None` at the moment the event fires — the same
signal `deliver_event_message` uses to decide between queuing and waking.

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

`_fire`'s own dispatch call is wrapped in a broad `try/except Exception` (logged at `error`)
before rescheduling — a `ChainedHookMessageUndeliverableError` (an idle-triggered event, see
"Available events" above) or any other hook-pipeline failure would otherwise skip
`_schedule_next()` and silently stop that entry from ever firing again.

### `WorkspaceTrustChanged`

Fires via `Session.fire_workspace_trust_changed_hook(reason)` at the two points a workspace's trust
decision can change against an already-live root session: the TUI's `>Trust workspace` command
(`reason="TrustCommand"`, `klorb/src/klorb/tui/mixins/workspace_bootstrap.py`) and ACP's
`_klorb/trustWorkspace` (`reason="AcpTrustWorkspace"`, `klorb.server.klorb_agent.KlorbAcpAgent`). Needs
no watcher/scheduler of its own — it dispatches directly through the same `HookDispatcher`/
`deliver_event_message` path the other two events use. Distinct from `onSessionStart`'s own
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
* **An automatic audit view of hook activity** — a TUI/VSCode view of every hook firing, its
  full `HookOutput`, and whether it errored, without a handler opting in via `HookOutput.log`
  (see "Debugging: `HookOutput.log`" above) or `logger.debug()`/`warning()` output.
* **An explicit turn-interrupt primitive** hooks/events can call directly, rather than
  `HookOutput.interrupt` needing new wiring on top of a turn's `cancel_event`
  (`klorb.session.events.TurnEventHandlers`) each time a caller wants it.
