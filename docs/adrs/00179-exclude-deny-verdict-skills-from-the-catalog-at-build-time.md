# Exclude `"deny"`-verdict skills from the catalog at build time

* Date: 2026-08-09 00:00
* Question: `SkillCatalog.discoverable()` already runtime-filters a `"deny"`-verdicted skill out
  of the available-skills interjection and `SearchSkills`, but the skill still lives in both
  `canonical()` and `typed()` — `ActivateSkill` reaches it and raises `PermissionError`, and a
  user's `/<name>` mention still resolves it. Should a skill already `"deny"`-verdicted when the
  catalog is first built (`build_catalogs()`) be excluded from both catalogs outright, or stay
  resolvable-but-blocked the way it was?
* Answer: Excluded outright. `build_catalogs()` now takes the session's `SkillRules` and skips a
  skill entirely — no `canonical()` entry, no `typed()` entry, no alias entries — the moment
  `evaluate_skill(skill_rules, skill_id) == "deny"` at scan time. It's absent from the
  available-skills interjection, `SearchSkills`, the vscode-plugin fuzzy finder
  (`Session.discover_skills()`), and any `/<name>` mention, exactly as if it didn't exist on disk.
  A skill denied *after* the catalog was already built (e.g. an interactive ask mid-session
  answered "deny") is unaffected by this — it stays resolvable in memory until an explicit reload,
  so `ActivateSkill`/`ReadSkillFile` still raise `PermissionError` for it and a leading mention is
  still skipped (now with a logged `logger.warning()`, since that skip is otherwise silent). See
  docs/specs/skills.md's "The session-scoped skill catalog" section.
* Reasoning: A skill pre-denied in `skillRules` before the catalog is ever built can never become
  anything other than `"deny"` for the lifetime of that built catalog — the catalog isn't rebuilt
  just because config changed, and nothing in this codebase mutates `skillRules` to *remove* a
  `"deny"` entry mid-session (only a fresh reload picks up an edited config file). Given that,
  keeping it resolvable-but-blocked bought nothing: every code path that could reach it already had
  to check the verdict before doing anything with it, so the only observable difference was cosmetic
  — the skill's `(namespace, name)`/`description` were still readable via `catalog.get()` even
  though nothing could ever activate it. Removing it from the catalog entirely is strictly simpler
  to reason about (a denied skill provably has zero footprint in what the model or a user-facing
  list can see) and removes a whole class of "is this skill resolvable but blocked, or truly
  absent" questions a future reader would otherwise have to answer by re-deriving the verdict logic
  at every call site.
