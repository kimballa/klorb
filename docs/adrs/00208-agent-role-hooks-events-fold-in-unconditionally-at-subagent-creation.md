# ADR 00208

**Date:** 2026-08-24

**Question:** `pair_programmer`'s live `FileSystemModified` watch was wired up through a
`disable-model-invocation` skill (`pair-programming-child`) that `internal:pair-programming` had
to activate by giving the new subagent's `initial_message` a leading `/pair-programming-child`
mention — a workaround, since the event subscription is really a property of the `pair_programmer`
role itself, not something that should depend on the creator remembering to invoke a specific
skill. How should an `agents.json` role declare its own hooks/events directly, and when should
they take effect on a subagent created as that role?

**Answer:**

* `AgentDefinition` (`klorb.agents.definition`) gains `hooks`/`events` fields, the same raw
  `{name: [handler, ...]}` shape a skill's `metadata.klorb.hooks`/`.events` frontmatter carries.
  `agent_hook_configs`/`agent_event_configs` parse them via new `klorb.hooks.merge.
  parse_session_scoped_hook_dict`/`parse_event_dict` helpers, factored out of what were
  `klorb.tools.skill.common.skill_hook_configs`/`skill_event_configs`'s own bodies so both the
  skill-grant and role-grant paths share one implementation instead of two near-identical copies.
* Unlike a skill grant (gated on activation, opt-in per session), a role's own `hooks`/`events`
  fold onto every subagent `klorb.agents.policy.plan_subagent_creation` builds for that role,
  unconditionally — the creator never has to know the grant exists. They're merged in after
  heritable-hooks/events filtering, via the same named-list-concatenate merge every other
  `hooks`/`events` layer uses, and `SubagentPlan` grows a `role_events` field carrying just the
  newly-granted `events` so `CreateSubagentTool.apply()` can start a `FileSystemWatcher`/
  `TimerScheduler` for them right after constructing the child `Session` — a subagent never fires
  its own `onSessionStart`, the moment that would otherwise do this.
* `pair_programmer`'s own `agents.json` entry now carries the `FileSystemModified` grant directly;
  `pair-programming-child` is deleted, its instructional body folded into `resources/
  system_prompts.d/roles/pair_programmer/default.md` (always active for the role, unlike a skill
  that needs activating), and `internal:pair-programming` no longer needs to give the new
  subagent's `initial_message` a leading skill mention.
* This mechanism is scoped to subagent creation only. A root session running directly as a role
  with its own `hooks`/`events` grant does not pick them up: `compute_root_session_grants` (the
  root-session analogue of `plan_subagent_creation`) narrows tool/skill/subagent-role sets but was
  left untouched here, since threading `hooks`/`events` through it would mean updating every one
  of its seven call sites for a case with no real usage today (specialist roles like
  `pair_programmer` are launched as subagents, not run at the top level).

**Reasoning:** Reusing the skill grant's exact wire shape (`{name: [handler, ...]}`, same
`isHeritable` default-`false` treatment) means a role author writes hooks/events the same way a
skill author already does, and lets the parser live in one place. Folding the grant in
unconditionally at creation time (rather than requiring an activation step) matches what
"a property of the role" actually means — a skill grant is opt-in because activating a skill is
itself a deliberate choice, but a role's own capability policy already applies unconditionally to
everything else `agents.json` governs (`restrict_to`, `agent_capabilities`), so hooks/events
should be no different. Explicitly starting watchers only for the newly-granted `role_events`
(not re-running `_start_event_watchers_for` over the child's whole merged `config.events`) avoids
double-registering a watcher for anything the child separately inherited as heritable from its
parent. Leaving `compute_root_session_grants` unchanged keeps this change scoped to the actual
problem (subagent creation), matching the same "don't generalize beyond what's asked" discipline
ADR 00204 followed for `is_heritable`.
