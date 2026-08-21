# ADR 00204

**Date:** 2026-08-20

**Question:** `metadata.klorb.bashCommands` lets a skill's frontmatter pre-authorize bash
commands into the activating session. The same mechanism was wanted for hooks and events
(`metadata.klorb.hooks`/`.events`). But `hooks`/`events` lived entirely on `ProcessConfig`, one
object shared identically by every `Session` in a process — there was no per-session handler set
to grant into. How should hook/event configuration move to support a per-session grant, and how
should a subagent's inheritance of its parent's hooks/events be controlled?

**Answer:** Split hook/event scope in two:

* `klorb.hooks.config.PROCESS_SCOPED_HOOK_NAMES` (`onProcessStart`/`onProcessEnd`/
  `onSessionStart`/`onSessionEnd`) stays on `ProcessConfig.hooks` — these fire before any session
  exists, or describe a session's own lifetime boundary, so they can't move to a per-session
  table. `ProcessConfig.events` is deleted outright: no event name is ever process-scoped.
* Every other hook name, and every event name, moves to new `SessionConfig.hooks`/`.events`
  fields. `load_process_config()` concatenates two on-disk sources into the root session
  template — the top-level `hooks`/`events` keys' own session-scoped portion, and new
  `sessionDefaults.hooks`/`.events` keys — and `HookDispatcher.dispatch`/`dispatch_event` read
  from the firing session's own `config.hooks`/`.events` for every non-process-scoped name,
  raising if no `session_config` is given.
* `HookConfig`/`EventConfig` each gain an `isHeritable: bool` (JSON `isHeritable`), read by
  `klorb.agents.policy.plan_subagent_creation` via `filter_heritable_hooks`/`filter_heritable_events`
  to decide what a subagent's `SessionConfig` keeps from its deep-copied parent. The default
  differs by source: `true` for a `HookConfig` parsed from `klorb-config.json` (tree-wide unless
  opted out), `false` for any `EventConfig` regardless of source (a standing watcher/timer isn't
  free), and `false` for a `HookConfig` parsed from a skill's `metadata.klorb.hooks` grant even
  though `HookConfig`'s own pydantic default is `true` — a skill's own grant shouldn't silently
  widen to every subagent the activating session happens to create.
* `Session.grant_skill_hooks`/`grant_skill_events` (mirroring the existing
  `grant_skill_bash_commands`) merge a skill's `metadata.klorb.hooks`/`.events` into the
  activating session's `config.hooks`/`.events` on activation, tracked per-skill-per-session
  (`SessionConfig.granted_skill_hook_event_ids`) so re-activating a skill doesn't re-register (and
  double-fire) its handlers — unlike an allow-list grant, a hook/event grant actually runs, so
  naive re-merging isn't naturally idempotent the way `bashCommands`' dedup-on-allow-list is.
  `grant_skill_events` also starts a `FileSystemWatcher`/`TimerScheduler` for any newly-granted
  entry immediately, via a generalized `Session._start_event_watchers_for(events)` callable on
  any session (root or subagent), not just once at root `onSessionStart`.
* `onSubagentStart`/`onSubagentTurnEnd` move to firing from the *parent* session's own
  `config.hooks`, describing the child via `HookInput`'s identifying fields but consulting the
  parent's handler chain — these are the parent's own observation of its child starting/finishing,
  not the child's. `onSubmitUserPrompt`/`onAgentTurnEnd` lose their previous root-only gate and
  now fire for any session's own turn, root or subagent, each consulting that session's own
  `config.hooks`.
* A dormant subagent (no live turn, no wake handler, but a parent) can now receive an event
  message: `Session._deliver_event_to_dormant_subagent` starts a fresh, uninterested turn
  directly, silently skipped (not delivered, not raised) if doing so would exceed
  `tools.subagents.maxConcurrentPerParent`/`maxActiveTotal`.
* A subagent's `reset_session()` (triggered by its own `onAgentTurnEnd`) wipes conversation state
  only — its `config` (role, tools, skills, heritability-filtered hooks/events) is left untouched,
  unlike a root session's, which reinitializes from the process template. `onSessionEnd` never
  fires for a subagent, reset or otherwise, including the `onAgentTurnEnd`-triggered reset's own
  `onSessionEnd` dispatch, which is now gated to root sessions only.

**Reasoning:** Splitting by `PROCESS_SCOPED_HOOK_NAMES` is the smallest boundary that still lets
`onProcessStart`/`onProcessEnd` dispatch with no session at all, while making every other hookable
moment naturally per-session — which is what a skill grant, and the whole-tree/parent-vs-child
scoping fixes, both need. Reusing `sessionDefaults` as a second on-disk source (rather than only
pushing the top-level `hooks`/`events` keys down) keeps hooks/events consistent with every other
per-session-mergeable key (`readDirs`, `commandRules`, ...), which already live under
`sessionDefaults`. Heritability defaults differ by source because the risk profile differs: a
config-file hook is deliberately authored to apply broadly, while a skill's own grant is a
side-effect of activation the user may not have separately vetted for every subagent it could
reach. Filtering only in `plan_subagent_creation` (not generalizing to every grant kind —
`command_rules`, `skill_rules`, directory/file grants) keeps this change scoped to what was asked;
generalizing `is_heritable` to those other grant kinds is logged in `TODO.md` rather than done
here.
