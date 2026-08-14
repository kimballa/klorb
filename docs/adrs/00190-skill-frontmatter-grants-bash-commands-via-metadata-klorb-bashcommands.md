# ADR 00190

**Date:** 2026-08-13

**Question:** A skill's `SKILL.md` sometimes needs `BashTool` commands (e.g. `git checkout`,
`git push`) that aren't otherwise safe to blanket-allow for a whole workspace. How should a skill
declare and receive those authorizations, given `klorb-config.json`'s `commandRules.allow` today
has no way to scope a grant to "only while this skill is in play"?

**Answer:** A skill's frontmatter may carry `metadata.klorb.bashCommands`, a list of
`commandRules`-shaped argv patterns. When the skill activates (after `skillRules`/
`onActivateSkill` have already let the activation through), `Session.grant_skill_bash_commands`
grants each pattern into `SessionConfig.command_rules.allow` at `scope="session"` — the same
in-memory, not-persisted-to-disk grant a `"session"`-scope answer to an interactive `BashTool` ask
applies.

**Reasoning:** `Skill.raw` already preserves a skill's entire frontmatter dict verbatim, so no
frontmatter-schema change was needed — reading a new nested key is enough. Session scope (not
workspace/homedir) keeps the exposure tied to the activating session's lifetime rather than
writing a standing grant to `klorb-config.json`, and reuses `klorb.permissions.command_grant.
apply_command_permission_grant` exactly as the existing `skillRules` ask-to-allow auto-promotion
does, rather than inventing a second grant-application path. Gating on `skillRules` first means
the actual trust decision is still "should this skill run at all" — a workspace that doesn't trust
a skill's author can deny the skill outright and this grant never fires; a granted pattern only
ever widens `allow`, never `deny`/`ask`, so it can't override an admin-level `deny`.
