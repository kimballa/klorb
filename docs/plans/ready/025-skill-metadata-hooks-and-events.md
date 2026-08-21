# Plan 025: Skill-Granted Hooks/Events + Session-Scoped Hooks/Events

## Context

`metadata.klorb.bashCommands` already lets a skill's frontmatter pre-authorize `BashTool`
commands into the activating session's `command_rules` (ADR 00190). The user wants the same
mechanism for hooks and events: `metadata.klorb.hooks`/`metadata.klorb.events` in a skill's
frontmatter, applied to the activating session when the skill activates.

Today, hooks and events live entirely on `ProcessConfig` (`hooks`/`events` fields), a single
object shared identically by every `Session` in a process — there is no per-session handler set
to append a skill grant into. Enabling this requires first moving most hook/event scope from
`ProcessConfig` down to `SessionConfig`, and then layering the skill grant on top. This also
fixes a related misdesign: `onSubagentStart`/`onSubagentTurnEnd` currently dispatch against the
*child* subagent's own hook list, when conceptually they're the *parent's* observation of its
child starting/finishing — and `onSubmitUserPrompt`/`onAgentTurnEnd` are wrongly gated to the
root session only, when they should fire for any session's own turn, subagent included.

This plan also seeds a general grant-heritability concept (`is_heritable`), scoped here to hooks
and events only; extending it to `command_rules`/`skill_rules`/directory-and-file grants is
explicitly out of scope, logged to `TODO.md` per the user's instruction.

## Design decisions (resolved)

* **Subagent `reset_session()`**: gate the `self.config = process_config.session.model_copy()`
  reinit line to root sessions only (`self.parent is None`), matching the existing
  `permission_framework_state` special-case beside it. A subagent's reset wipes conversation
  state only; its `role_name`/`skill_rules`/`hooks`/`events` are untouched.
* **`ProcessConfig.events`**: delete the field entirely (it can never hold data once every event
  name is session-scoped) along with its `PROCESS_KEY_MAP` entry.
* **Skill hook/event grant idempotency**: track granted skill ids in a new per-session
  `set[tuple[str, str]]` (mirrors nothing existing exactly, closest analogue is `approved_scopes`
  on `SessionConfig`) so re-activating the same skill in one session does not re-register (and
  thus double-fire) its `metadata.klorb.hooks`/`.events` handlers.
* **Dormant-subagent event wake**: `parent_interested=False`, matching
  `dispatch_direct_message`'s existing dormant-child semantics for a human-sent message — the
  event-triggered turn's output stays out of the parent's own conversation until the parent
  explicitly calls `WaitForSubagent`/`MessageSubagent`.
* **`onSessionEnd` on a subagent's reset**: `onSessionEnd` doesn't apply to subagents at all — it
  never fires for one, reset or otherwise. Both reset branches
  (`turns.py::_fire_agent_turn_end_hook`, `core.py::_deliver_or_reset_event`) that unconditionally
  dispatch `onSessionEnd` before resetting must gate that dispatch to `self.parent is None`.
* **`is_heritable` default differs by source, not just by hook-vs-event.** `HookConfig.is_heritable`'s
  pydantic field default is `True` — this is what governs a hook parsed from `klorb-config.json`
  (top-level `hooks` or `sessionDefaults.hooks`) when the author omits `isHeritable`. A hook parsed
  from a skill's `metadata.klorb.hooks` gets the *opposite* default when omitted: `False`, matching
  `EventConfig`'s own default regardless of source. `skill_hook_configs` (§8) must apply this
  explicitly — force `is_heritable=False` on any parsed entry whose raw dict didn't set
  `isHeritable`, rather than relying on `HookConfig`'s own field default, which would otherwise
  silently give a skill-granted hook the config-file default instead.
* **`onSubagentTurnEnd`'s chained continuation** lands on the **child's** own conversation
  (`child._deliver_chained_hook_message(...)`), even though the handler chain that produced it is
  the parent's — the parent observes/reacts, but the continuation is the child's own next turn.

## Architecture

### 1. Schema: `is_heritable` + process-scoped hook names

`klorb/src/klorb/hooks/config.py`:

* Add `PROCESS_SCOPED_HOOK_NAMES: frozenset[str] = frozenset({"onProcessStart", "onProcessEnd", "onSessionStart", "onSessionEnd"})`.
* `HookConfig` gains `is_heritable: bool = Field(default=True, alias="isHeritable")`; add
  `model_config = ConfigDict(populate_by_name=True)` (not currently set on `HookConfig`, only on
  `HookConfigFilter`).
* `EventConfig` gains the same field, default `False` — subclasses inherit it unchanged.
* Add `filter_heritable_hooks(dict[str, list[HookConfig]]) -> dict[str, list[HookConfig]]` and
  `filter_heritable_events(...)`: pure functions returning a new dict with only `is_heritable`
  entries, dropping any name left empty.

### 2. `SessionConfig` gains `hooks`/`events`

`klorb/src/klorb/session/config.py`: add `hooks: dict[str, list[HookConfig]]` and
`events: dict[str, list[EventConfig]]`, both `Field(default_factory=dict)`, plus
`_granted_skill_hooks_events: set[tuple[str, str]] = Field(default_factory=set)` (or an
equivalent name) for the idempotency tracking above.

Unlike `role_name`/`workspace`, these **do** have an on-disk key: `sessionDefaults.hooks`/
`sessionDefaults.events`, session-scoped-names-only, merged by named-list concatenation exactly
like the top-level `hooks`/`events` keys already are — join the list of keys `SESSION_KEY_MAP`'s
docstring already calls out as "handled separately... ahead of `_route_keys()`" alongside
`readDirs`/`writeDirs`/etc., since this is a concatenate-merge field, not a 1:1 scalar
`SESSION_KEY_MAP` entry.

`klorb.hooks.config` imports nothing from `klorb.session`/`klorb.process_config`, so this is a
safe module-level import — no deferred-import dance needed.

### 3. Config-load: two on-disk sources concatenate into the session template

`load_process_config()` now has two `hooks`/`events` sources per layer:

* **Top-level `hooks`/`events`** (unchanged parsing) — every hook/event name is valid here,
  merged into `concatenated_hooks`/`concatenated_events` as today.
* **New: `sessionDefaults.hooks`/`sessionDefaults.events`** — parsed the same way
  (`parse_handler_list`, same per-entry pydantic validation), merged into their own
  `concatenated_session_hooks`/`concatenated_session_events` accumulators. A name in
  `PROCESS_SCOPED_HOOK_NAMES` appearing here is a config error: dropped and reported into
  `config_warnings`, the same "config parse error" mechanism `klorb.hooks.merge.parse_handler_list`
  already uses for an entry that fails pydantic validation — process-scoped hooks may only be
  configured via the top-level `hooks` key. (No equivalent restriction for events: no event name
  is ever process-scoped.)

After both are built, split `concatenated_hooks`/`concatenated_events` (the top-level source) by
name: `PROCESS_SCOPED_HOOK_NAMES` entries go to `ProcessConfig.hooks`, unchanged. Every other hook
name, and every event name, concatenate onto `process_config.session.hooks`/`.events` — first the
top-level source's entries, then `sessionDefaults`'s own entries for that same name appended after
(within one layer; normal layer-ordering applies across layers) — the template a fresh root
`Session`'s `config` is copied from. Update `ProcessConfig.hooks`'s docstring (now holds only the
4 process-scoped names) and delete `ProcessConfig.events` + its `PROCESS_KEY_MAP` entry per the
resolved decision above. Update `process_config_to_disk_dict()` to reconstruct the top-level
on-disk `hooks` key from `{**process_config.session.hooks, **process_config.hooks}` (mutually
exclusive by construction) and top-level `events` from `process_config.session.events` — with
`sessionDefaults.hooks`/`.events` themselves not reconstructable from the merged runtime state
(same limitation `readDirs`/`writeDirs` already have post-merge), so `process_config_to_disk_dict()`
folds them into the same top-level keys rather than trying to separate them back out.

### 4. `HookDispatcher` branches on process-scoped vs. session-scoped

`klorb/src/klorb/hooks/dispatcher.py::dispatch()`: for a name in `PROCESS_SCOPED_HOOK_NAMES`,
look up `self._process_config.hooks`; otherwise require `session_config` (raise `ValueError` if
`None` — every real call site always supplies one now) and look up `session_config.hooks`.
`dispatch_event()` always requires `session_config` and reads entries from whatever the caller
already selected (unchanged shape) but no longer has a `ProcessConfig`-level fallback source.
`cli/main.py`'s two `onProcessStart`/`onProcessEnd` call sites are unaffected (process-scoped,
no session exists yet).

### 5. `onSubagentStart`/`onSubagentTurnEnd` fire from the parent, describing the child

`Session._dispatch_hook` gains an optional `subject: Session | None = None` param (defaults to
`self`) used only for the `HookInput`'s identifying fields (`session_id`/`role`/`workspace_root`)
— handler-chain lookup always uses `self.config.hooks`. `fire_subagent_start_hook`/
`fire_subagent_turn_end_hook` move to being called on the **parent**, taking the child explicitly:
`parent.fire_subagent_start_hook(child, message)` / `parent.fire_subagent_turn_end_hook(child, result)`.
`klorb/src/klorb/agents/policy.py::_run_subagent_turn`'s three call sites update accordingly.
`klorb/src/klorb/session/mixins/_base.py`'s stub signatures update to match.

### 6. `onSubmitUserPrompt`/`onAgentTurnEnd` — whole tree

`klorb/src/klorb/session/mixins/turns.py::_dispatch_turn`: delete the `is_root` gate entirely —
both hooks already call `self._dispatch_hook(...)` with `session_config=self.config`, so once
ungated, a subagent's own turn naturally consults its own (heritability-filtered) `config.hooks`.
Apply the two `onSessionEnd`-on-reset and `reset_session()`-scope-preservation fixes from
"Design decisions" above as part of this step, since this is what makes them reachable for a
subagent for the first time.

### 7. Dynamic per-session event watchers

Generalize `Session._start_workspace_event_watchers()` into `_start_event_watchers_for(events)`,
callable on any session (root or subagent), repeatable (new entries only) — start a
`FileSystemWatcher`/`TimerScheduler` for whichever of `events`'s `FileSystemModified`/`Timer`
keys are non-empty, registering each under a collision-free `register_teardown` key (a monotonic
per-session counter). `fire_session_start_hook` calls it with `self.config.events` (was
`self._process_config.events`, now deleted). `fire_workspace_trust_changed_hook` reads
`self.config.events.get("WorkspaceTrustChanged", [])` and drops its `self.parent is not None`
early return (keeps the `_process_config is None` one) — no longer inherently root-only, though
no new call site is required by this plan.

`Session.deliver_event_message` gains a third branch: when no turn is running and no wake
handler is registered but `self.parent is not None` (an ordinary dormant subagent), start a
fresh turn directly via `klorb.agents.policy.dispatch_subagent_turn`, `parent_interested=False`,
*unless* doing so would exceed `subagents_max_concurrent_per_parent`/`max_active_total`, in which
case skip silently (log at `info`) rather than raise or deliver. Refactor
`policy.py::check_concurrency_limits` to share its two checks with a new non-raising
`concurrency_limits_exceeded() -> bool` for this path.

### 8. Skill-frontmatter parsing + grants

`klorb/src/klorb/tools/skill/common.py`: add `skill_hook_configs(raw)` and
`skill_event_configs(raw)`, mirroring `skill_bash_command_patterns`. Each reads
`metadata.klorb.hooks`/`.events`, validates via `klorb.hooks.merge.parse_handler_list` (same
per-entry validation `load_process_config()` uses), and drops+logs a `logger.warning()` for: an
unrecognized hook/event name, a `PROCESS_SCOPED_HOOK_NAMES` entry (process-scoped hooks may only
be configured via `klorb-config.json`'s top-level `hooks` key, never a skill grant), or a
malformed shape — same "log only, no session-level warnings sink" treatment
`skill_bash_command_patterns` already gives a malformed `bashCommands` entry. A `Timer` event
entry still runs through `clamp_timer_intervals`. Per "Design decisions" above, `skill_hook_configs`
must force `is_heritable=False` on any parsed `HookConfig` whose raw frontmatter dict omitted
`isHeritable` — `HookConfig`'s own pydantic default (`True`) is correct for config-file-authored
hooks but wrong for skill-granted ones.

`klorb/src/klorb/session/mixins/skills.py`: add `grant_skill_hooks(skill)`/
`grant_skill_events(skill)`, mirroring `grant_skill_bash_commands` — checked against and added to
the new granted-skill-ids tracking set first (idempotency), then merged into `self.config.hooks`/
`.events` by **reassigning a new dict** (never mutating in place — a root session's `config.hooks`/
`.events` are the *same object* as `ProcessConfig.session.hooks`/`.events` after the plain
`model_copy()` construction, shared across every session built from that template, so in-place
mutation would leak across sessions). `grant_skill_events` also calls
`self._start_event_watchers_for(new_events)` with just the newly-added entries. Both called
from `ActivateSkillTool.apply()` and `_build_user_skill_activation_interjection`, right after the
existing `grant_skill_bash_commands` call in each.

### 9. Subagent-creation heritability filtering

`klorb/src/klorb/agents/policy.py::plan_subagent_creation()`, right after the existing
`child_config = parent.config.model_copy(deep=True)`: apply `filter_heritable_hooks`/
`filter_heritable_events` to `child_config.hooks`/`.events`. Safe to reassign in place post-deep-copy
without touching the parent's own dicts. `compute_root_session_grants()` (no parent) needs no
change.

### 10. TODO.md

Add a note (new `### Plan 025: ...` section) that grant heritability, seeded here for hooks/events
only, should generalize to a shared `is_heritable` flag on every grant kind
(`command_rules`/`skill_rules`/`read_dirs`/`write_dirs`/`read_files`/`write_files`) so a
subagent's creation can filter those the same way — not addressed by this plan.

## Critical files

* `klorb/src/klorb/hooks/config.py` — schema (`is_heritable`, `PROCESS_SCOPED_HOOK_NAMES`, filter helpers)
* `klorb/src/klorb/session/config.py` — `SessionConfig.hooks`/`.events`/granted-skill-ids tracking
* `klorb/src/klorb/process_config.py` — push-down split, `ProcessConfig.events` removal, disk round-trip
* `klorb/src/klorb/hooks/dispatcher.py` — process-scoped vs. session-scoped branching
* `klorb/src/klorb/session/mixins/core.py` — `_dispatch_hook` subject param, subagent hook signatures, dynamic watcher attach, event-trust hook
* `klorb/src/klorb/session/mixins/turns.py` — remove `is_root` gate, dormant-subagent wake, reset-scope fixes
* `klorb/src/klorb/session/mixins/_base.py` — updated stub signatures
* `klorb/src/klorb/agents/policy.py` — parent-fires call sites, heritability filtering, `concurrency_limits_exceeded`
* `klorb/src/klorb/tools/skill/common.py` — `skill_hook_configs`/`skill_event_configs`
* `klorb/src/klorb/session/mixins/skills.py` — `grant_skill_hooks`/`grant_skill_events`
* `klorb/src/klorb/tools/skill/activate_skill.py` — grant call site
* `docs/specs/hooks-and-events.md` — scope table (`onSessionStart`/`End` → process;
  `onSubmitUserPrompt`/`onAgentTurnEnd` → whole tree; `onSubagentStart`/`TurnEnd` → parent agent
  session), "Available events" root-only text replaced with the user's supplied heritability
  paragraph, `is_heritable`/`isHeritable` documented in "Config schema"
* `docs/specs/skills.md` — new `metadata.klorb.hooks`/`metadata.klorb.events` section beside the
  existing `bashCommands` one
* `docs/user/hooks.md` — user-facing `isHeritable` key documentation
* New ADR at `docs/adrs/00204-...md` (find actual next number via `ls docs/adrs/ | tail -1` at
  implementation time) recording the SessionConfig-level hooks/events + heritability design
* `TODO.md` — heritability-generalization note

## Test coverage

* `klorb/tests/klorb/hooks/` — `is_heritable` default/alias round-trip, `filter_heritable_*`,
  `PROCESS_SCOPED_HOOK_NAMES`, dispatcher branching + `ValueError` on missing `session_config`
* `klorb/tests/klorb/test_process_config.py` — push-down split, disk round-trip, `ProcessConfig.events`
  removal, `sessionDefaults.hooks`/`.events` parsing + concatenation with the top-level source,
  process-scoped name rejected from `sessionDefaults.hooks` with a `config_warnings` entry
* `klorb/tests/klorb/session/` — `onSessionStart`/`End` stay process-scoped; `onSubagentStart`/`TurnEnd`
  fire from parent's handler set only (add-to-parent-only and add-to-child-only cases);
  `onSubmitUserPrompt`/`onAgentTurnEnd` now fire for a subagent's own turn; subagent
  `reset_session()` preserves config; dynamic watcher start (root, mid-session skill grant,
  subagent); dormant-subagent event wake incl. concurrency-cap silent skip
* `klorb/tests/klorb/tools/skill/` — `skill_hook_configs`/`skill_event_configs` parsing +
  rejection cases; `ActivateSkillTool.apply()` grant call sites; re-activation idempotency
* `klorb/tests/klorb/agents/test_policy.py` — heritability filtering in `plan_subagent_creation`;
  `concurrency_limits_exceeded`

## Verification

1. `make -C klorb lint typecheck` after each phase.
2. `make -C klorb TEST_SUITE=hooks test`, `TEST_SUITE=skill test`, `TEST_SUITE=subagent test`,
   `TEST_SUITE=process_config test` scoped runs during the dev loop.
3. One full unscoped `make -C klorb test` before declaring done.
4. `make lint_docs` from repo root after any docs/spec/ADR/TODO.md edit.
5. Manual smoke check: a skill with `metadata.klorb.events.FileSystemModified` activated inside a
   subagent, then the watched file touched while that subagent is dormant — confirm it wakes and
   its output does not appear in the parent's conversation until `WaitForSubagent` is called.
