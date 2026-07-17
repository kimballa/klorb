# Discover `.claude/skills/` as a second `workspace`-namespace source, not a fourth tier

* Date: 2026-07-17 00:00
* Question: The skills feature (docs/specs/skills.md) discovers skills from three tiers —
  `workspace` (`${workspace_root}/.klorb/skills/`), `user` (`$KLORB_DATA_DIR/skills/`), and
  `internal` (packaged). The skills plan (`docs/plans/archive/010-skills.md`) listed a
  `compatibility.claudeSkills` toggle — which additionally discovers `${workspace_root}/.claude/
  skills/`, mirroring `compatibility.claudeMarkdown` for `CLAUDE.md` — as explicitly *out of
  scope*, noting only that "a fourth discovery tier gated the same way is the expected shape when
  it is." When it was pulled into scope, should `.claude/skills/` be a **fourth namespace** (its own
  `(namespace, name)` identity, e.g. `claude`), or a **second source directory for the existing
  `workspace` namespace**?
* Answer: A second source directory for the existing `workspace` namespace. When
  `compatibility.claudeSkills` is `True` and the workspace is trusted,
  `${workspace_root}/.claude/skills/` is scanned right after `${workspace_root}/.klorb/skills/`,
  and every skill found in either directory has `namespace == "workspace"`. Both directories are
  gated identically on `SessionConfig.workspace.trusted`. On a name collision, `.klorb/skills/`
  (klorb's own convention) wins over `.claude/skills/`, per the same most-specific-source-wins
  shadowing every tier already uses (`klorb.tools.skill.common.resolve_all_skills` takes the first
  occurrence of a name in precedence order and skips the rest). `compatibility.claudeSkills` is a
  top-level `klorb-config.json` key backing `ProcessConfig.compatibility_claude_skills` (default
  `false`), read by discovery — plumbed through `Session._compatibility_claude_skills` for the
  interjections and off `ToolSetupContext.process_config` for the tools — exactly like
  `compatibility_claude_markdown`.

  klorb reads only the `description` field from a `.claude/skills/*/SKILL.md`; Claude Code's own
  frontmatter may carry a `name`, allowed-tools lists, or model hints, all of which are ignored.
  A Claude-authored skill is therefore discovered by its directory basename with its `description`
  listed, and is otherwise an ordinary `workspace` skill.
* Reasoning: A skill's namespace is its *trust tier* and the axis its `skillRules` verdict is keyed
  on, not a label for which directory on disk it came from. A `.claude/skills/foo` skill and a
  `.klorb/skills/foo` skill are the same kind of thing — project-supplied instructions, trusted
  exactly when the workspace is trusted, authored by whoever controls the repository. Giving
  `.claude/skills/` its own `claude` namespace would fragment that single trust decision into two
  and force users (and the pre-populated `default-config.json` grants) to reason about a fourth
  identity that adds no security distinction: nothing about a skill living under `.claude/` rather
  than `.klorb/` makes it more or less trustworthy than the workspace it ships in. Folding it into
  `workspace` keeps the permission model's `(namespace, name)` identity — and the shadowing rule
  that closes the "a repository hijacks a grant made for a same-named skill" risk — intact, with no
  new namespace token to thread through `SkillRules`, `skill_grant`, or the config docs.

  The plan's "fourth tier" phrasing was written before the feature was built, when the interaction
  with the `(namespace, name)` permission identity hadn't been worked through; treating `.claude/`
  as another *source* of the `workspace` *tier* is the same outcome ("`.claude/skills/` is also
  discovered, gated on trust") without minting a namespace that would have to justify its own
  existence in every rule table. The trade-off accepted is that a `.klorb/skills/foo` and a
  `.claude/skills/foo` cannot both be independently addressable at once — the former shadows the
  latter — which matches how a project that has migrated a skill from one convention to the other
  should behave anyway (one wins, deterministically), and mirrors the single-winner shadowing
  across the `workspace`/`user`/`internal` tiers themselves.
