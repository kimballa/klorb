2026-08-16

## Question

Adding a skills catalog to the shared search-index store (the anticipated follow-up flagged in
docs/adrs/00198-memories-catalogs-share-search-index-storage-scoped-by-catalog-partition-key.md)
raises questions the two memories catalogs don't: skills span three tiers (`user`/`workspace`/
`internal`), not two; one tier (`internal`) is a packaged `importlib.resources` `Traversable`, not
a real filesystem path, so it has no `.stat().st_mtime`; and a skill's own content spans multiple
markdown files (`SKILL.md` plus any nested resource file), not one file per entry.

## Answer

**Three catalogs, not one.** `skills-user`/`skills-workspace`/`skills-internal`, mirroring
`memories-global`/`memories-workspace` exactly — each namespace gets its own `vec0` partition and
its own slot in `SearchSkillsTool`'s `namespace` filter, the same shape `SearchMemoriesTool`
already uses for its own `namespace` filter.

**One synthetic virtual path scheme for all three tiers**, including `workspace` (whose skill
files do have a real, on-disk workspace-relative path): every chunk's `source_path` is
`.klorb/skills-index/<skill-name>/<relative-path-within-skill-dir>`, regardless of which tier the
skill came from. Uniform handling across tiers beats a real-path/synthetic-path branch per tier,
and never collides with a real workspace file since `_walk_indexable_files` always skips `.klorb`.

**No mtime fast path — every scan reads and content-hashes every skill markdown file.** The
`internal` tier's `Traversable` has no `.stat()`, and skill corpora are small (tens of files
across all tiers), so a per-tier stat/no-stat special case isn't worth the complexity a uniform
"always hash-compare" pass avoids. A sentinel `last_modified_ts=0.0` is stored per file; nothing
ever reads it back for this catalog. The existing "hash unchanged → skip rechunk/embed"
short-circuit already in `WorkspaceIndexer._reindex_file`'s callers keeps a no-op rescan cheap.

**No live filesystem watcher for skill directories.** The three tiers span a real
workspace-relative dir, a real homedir-rooted dir, and a non-filesystem packaged resource; the
existing `watchdog`-based watcher only knows how to watch real paths. Refreshing happens on the
next session start (initial scan) or an explicit `klorb index scan` — the same "stale until an
explicit rescan" trade-off docs/specs/skills.md's "Known risks" already accepts for the *display*
skill catalog (`>reload skills`), just applied to the search index too.

**Indexing runs independent of `skillRules`.** Like memories (which has no access-control concept
at all), the indexer scans every discoverable skill on disk regardless of `skillRules`.
`SearchSkillsTool.apply()` filters every hit — literal or semantic — through
`registry.canonical().discoverable(skill_rules)` before returning it, so a denied, removed, or
precedence-shadowed skill's semantic hit is silently dropped rather than ever reaching a result.

## Reasoning

Indexing runs independent of `skillRules` because indexing itself never discloses anything: a
`SearchSkills` result is always `{namespace, name, description}`, never a chunk's text or a file
path, so there's nothing in the index a denied skill's presence there could leak — the actual
gate (whether the model can act on a hit at all) is enforced once, at query time, the same
`discoverable()` set the existing literal search already used before this catalog existed.

The always-hash-compare choice trades a small, constant per-scan cost (reading and hashing every
skill markdown file, currently on the order of tens of files) for not having to special-case
`Traversable`-vs-`Path` staleness detection. If the skill corpus ever grows enough for this to
show up in scan timing (`_ScanPhaseTimings`'s `read`/`chunk` totals), a real-mtime fast path for
the `workspace`/`user` tiers specifically (both real `Path` roots) is the natural follow-up,
leaving `internal` as the one tier that must stay hash-only.
