# Build the skill catalog once per process (not per tool call, and not per `Session`)

* Date: 2026-07-19 00:00
* Question: `SearchSkills`, `ActivateSkill`, `ReadSkillFile`, and `Session`'s skill interjections
  each used to walk the filesystem themselves (`klorb.tools.skill.common.resolve_all_skills`/
  `discover_skills`/`resolve_skill`) on every call. As the skill-mention-resolution surface grew
  (aliases, colon-qualified references, a leading-mention unconditional-activation check), that
  disk scan started happening several times per turn. Should discovery stay per-call, move to
  per-`Session` (rebuilt whenever a fresh `Session` replaces the live one, e.g. on `/clear`), or
  become a single process-wide structure built once and reused everywhere?
* Answer: Process-wide, built once. `klorb.tools.skill.catalog` holds two module-level
  `SkillCatalog`s (`canonical_catalog()`/`typed_catalog()`), built by a single disk scan
  (`build_catalogs()`) the first time either is needed (`ensure_skill_catalog()` — a cheap no-op
  after the first call), and never touched again except by an explicit `reload_skill_catalog()` —
  the ">Reload skills" command-palette action. Deliberately *not* tied to `Session`'s lifetime: a
  `/clear` replacing the live `Session` does not rebuild the catalog, since the catalog is
  independent process state, not session state — the same reasoning that keeps `ProcessConfig`
  itself outside `SessionConfig`.
* Reasoning: A skill's `SKILL.md` frontmatter and the directory tree under it change rarely
  compared to how often a turn might mention or search for one — re-walking three tiers'
  filesystem trees (plus, for each hit, reading and YAML-parsing its frontmatter) on every
  `SearchSkills` call, every `ActivateSkill` call, and every turn's mention scan is pure waste for
  a resource that's effectively static for the life of a klorb invocation. Building it once and
  reusing it in memory turns every one of those into a plain dict lookup. Scoping it to the
  process rather than the `Session` avoids a subtler cost: `/clear` is a cheap, frequent action
  (a fresh conversation, same workspace) that shouldn't imply a filesystem re-walk just because a
  new `Session` object was constructed — nothing about starting a new conversation implies the
  skills on disk changed. The trade-off accepted is staleness: a skill added, edited, or removed on
  disk after the catalog was built is invisible until an explicit `>reload skills` (or a fresh
  process). This is judged acceptable because skill authoring is already a deliberate,
  infrequent, privilege-escalation-gated action (see "Creating and editing skills" in
  docs/specs/skills.md) — the author is in a position to reload afterward — rather than something
  that needs to silently take effect mid-session.
