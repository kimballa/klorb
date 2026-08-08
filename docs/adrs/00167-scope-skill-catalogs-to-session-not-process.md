# Scope skill catalogs to `Session`, not the process

* Date: 2026-08-01 00:00
* Supersedes: [[build-the-skill-catalog-once-per-process-not-per-call]]'s "process-wide, not
  per-`Session`" half.
* Question: `klorb.tools.skill.catalog.SkillCatalogRegistry` held its two `SkillCatalog`s
  (`typed`/`canonical`) as a single process-wide singleton, reached via
  `get_skill_catalog_registry()`, deliberately outliving any one `Session` (a `/clear` did not
  rebuild it). Should catalogs stay process-wide, or move onto `Session` so each session owns and
  rebuilds its own?
* Answer: Per-`Session`. `Session` owns one `SkillCatalogRegistry` instance
  (`Session.skill_catalog_registry`), constructed fresh in `SessionCoreMixin.__init__` alongside
  its `ToolRegistry` — the same "constructed externally or in `__init__`, referenced back from
  `ToolSetupContext`, never a bare module global" shape `ToolRegistry` already used. A skill
  `Tool`'s `apply()` reaches it via `context.session.skill_catalog_registry` (through the new
  `klorb.tools.skill.catalog.resolve_session_skill_catalog_registry()` helper, which raises
  `ValueError` if `context` wasn't built with a real `Session`); `Session`'s own skill
  interjections use `self._skill_catalog_registry` directly. Catalogs are still built lazily, from
  a single disk scan, and reused in memory for that instance's lifetime — the "once, not per tool
  call" half of the prior ADR's reasoning is unchanged. What changed is *whose* lifetime: a new
  `Session` — a fresh interactive session, a restored one, or a `/clear` — starts with an empty
  registry and rescans the disk on its own first use, rather than inheriting whatever a previous
  session (or process) already built. Catalogs are not persisted to `session.json`; a restored
  session rebuilds them the same way a brand-new one does. `Session.reload_skills()` is the new
  single entry point for an explicit rebuild — the ">Reload skills" command palette action and the
  `_klorb/reloadSkills` ACP extension both now just call it, and it's also what a workspace
  trust-state change (`_apply_workspace_config`) calls internally, rather than each caller
  reaching into the registry directly.
* Reasoning: A process-wide catalog was a reasonable simplification when klorb only ever ran one
  session at a time end-to-end, but it means a second concurrent session in the same process (or a
  test suite exercising several sessions back-to-back) either shares stale state across sessions
  with different workspaces, or requires a test-only `reset_for_tests()` escape hatch to avoid
  cross-test contamination (see the removed `SkillCatalogRegistry.reset_for_tests()` and
  `conftest.py`'s prior `_reset_skill_catalog` fixture). Scoping the registry to `Session` — the
  same unit `ToolRegistry`, `tool_state`, and `scratchpad` are already scoped to — removes that
  whole class of cross-session leakage for free: two sessions with different workspaces simply
  each get their own catalog, and a test that constructs its own `Session` needs no reset fixture
  at all. The trade-off from the prior ADR (a skill added/edited/removed on disk is invisible
  until an explicit reload) is unchanged in kind, just now scoped per-session: a fresh `Session`
  already rescans on its own, so the staleness window that matters is only ever "this one running
  session, since it last rebuilt its catalog" — narrower than "this whole process, since it
  started," which is a strict improvement.
