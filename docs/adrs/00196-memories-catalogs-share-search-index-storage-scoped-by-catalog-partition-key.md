2026-08-16

## Question

Adding `memories-global`/`memories-workspace` search-index catalogs alongside the existing
`workspace` catalog raises two linked storage questions. First: does each catalog get its own
SQLite database (own `SearchIndexStore`, own owner lock, own background watcher), or do catalogs
share one store with `catalog` as a real filtering dimension inside it — and if shared, can
`sqlite-vec`'s `vec0` KNN search be scoped to one catalog without a large catalog crowding out a
small one's true nearest neighbor in an unfiltered top-`k` search? Second, specifically for
`memories-global`: that catalog isn't workspace-rooted, since it applies across every workspace —
does it get one store shared by every klorb process across every workspace (requiring
cross-process, cross-workspace, cross-klorb-version synchronization of a single database), or does
each workspace's own indexer carry its own redundant copy?

## Answer

One store per workspace, covering all three catalogs — `workspace`, `memories-workspace`, and
`memories-global` — sharing `WorkspaceIndexer`'s single owner lock, watcher, and thread pool.
`memories-global` is walked from `KLORB_DATA_DIR/memories/` (outside the workspace root) but
indexed redundantly into each workspace's own `workspace.db`, never into one store shared across
workspaces or processes. This means global-memory indexing is now gated by the same
`search_workspace_index_enabled`/workspace-trust/embedding-availability conditions as the
`workspace` catalog itself — accepted as a real trade-off (global memories stop being
semantically searchable in an untrusted workspace) in exchange for never having to reconcile one
shared database file across independently-versioned klorb processes on unrelated workspaces.

Catalog isolation inside the shared store is enforced at the SQL level: `chunks_vec` declares
`catalog TEXT PARTITION KEY`, and `chunks_fts` gets a plain `catalog UNINDEXED` column; every
`search_lexical`/`search_vector`/`hybrid_search` call takes a required `catalog` argument.
Verified empirically against the pinned `sqlite-vec==0.1.9` before committing to this design:
seeding 2000 unrelated vectors in one catalog and 3 real matches in another, an unfiltered top-3
KNN query returned the noise catalog's vectors ahead of the real matches, while a
`catalog`-filtered query with the same `k` returned exactly the two true within-partition nearest
neighbors. FTS5's `MATCH` has no equivalent problem, since it doesn't truncate results before
other `WHERE` predicates apply.

`memories-workspace`'s `Chunk.source_path` is a real workspace-root-relative path
(`.klorb/memories/notes.md`); `memories-global`'s is a synthetic `.klorb`-rooted path
(`.klorb/global-memories/notes.md`) standing in for a file that isn't really under the workspace
root at all, chosen specifically so it can never collide with a real `workspace`-catalog path in
the shared `files`/`chunks` bookkeeping (`_walk_indexable_files` always skips `.klorb`).

## Reasoning

Two earlier designs were considered and briefly implemented before converging here. A separate
SQLite database per catalog sidesteps the KNN crowding-out question by construction, but the
operational cost compounds with every catalog added (a `skills`-flavored catalog is already
anticipated): N independent owner/write locks for what's conceptually one workspace's index, two
klorb processes on the same workspace tree independently "owning" different catalogs of the same
tree (which has no coherent meaning), and `klorb index scan --threads` having no sane
interpretation across N independently-lifecycled indexers. A single process-wide store for
`memories-global` (one `MemoryCatalogIndexer` singleton, shared by every workspace in the process)
solves that but reintroduces the same class of problem across processes instead of within one:
two klorb instances on different workspaces, potentially different versions, racing to open and
write the same database file, with a schema-version bump in one silently rebuilding the index out
from under the other. Since global memories are expected to stay small, redundant per-workspace
copies are cheap, and folding them into `WorkspaceIndexer` removes both problems at once — at the
cost of the trust-gating trade-off described above.
