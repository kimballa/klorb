# Leading skill mention auto-promotes `"ask"` to `"allow"` with no interactive prompt

* Date: 2026-08-09 00:00
* Question: A user's message starting with `/<name>` for an `"allow"`-verdicted skill already
  unconditionally activates it (`UserSkillActivation`, no `ActivateSkill` round trip). For an
  `"ask"`-verdicted skill, should that same leading mention (a) fall back to the ordinary
  `SkillReference` reminder, requiring the model to call `ActivateSkill` and the user to click
  through an interactive ask panel, or (b) treat the act of typing `/<name>` itself as the user's
  approval and activate immediately?
* Answer: (b). A leading `/<name>` mention auto-promotes an `"ask"`-verdicted skill to `"allow"`
  for the rest of the session (`klorb.permissions.skill_grant.
  apply_skill_permission_grant(action="allow", scope="session", ...)`) and then activates it the
  same way an already-`"allow"` skill would — no `PermissionAskContext` is ever raised for this
  path. This only ever widens `"ask"` to `"allow"`, never touches `"deny"`, and the grant is
  session-scoped only (never persisted to a config file, exactly like an interactive "Allow (this
  session)" answer). See docs/specs/skills.md's "Leading skill mention" section.
* Reasoning: `"ask"` exists to interrupt the model *before it autonomously decides* to load a
  skill's instructions, on the theory that a skill's `SKILL.md` is a materially bigger context
  injection than its one-line description. That rationale doesn't apply when the user themselves
  types the skill's name as the leading token of their own message — that's not the model deciding
  anything, it's the user directly invoking a specific skill by name, the same intent an
  interactive "Allow" click expresses. Requiring a *second* confirmation (an ask panel) for an
  action the user just took *first* would be redundant friction with no security benefit: the user
  could always get to the same `"allow"` state by clicking through the panel anyway, so gating on
  it only adds a click, not a real gate. Scoping the promotion to the session (not persisting it)
  keeps the blast radius equivalent to what an interactive "session" answer would have produced,
  and confining it to widening `"ask"`→`"allow"` (never touching `"deny"`) means a skill an admin
  or the user explicitly denied stays denied regardless of what the user types.
