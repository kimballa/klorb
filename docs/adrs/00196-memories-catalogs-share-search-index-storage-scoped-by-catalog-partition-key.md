2026-08-16

## Question

Adding `memories-global`/`memories-workspace` search-index catalogs alongside the existing
`workspace` catalog raises a storage question: does each catalog get its own SQLite database
(own `SearchIndexStore`, own owner lock, own background watcher), or do catalogs that share a
lifecycle scope share one store, with `catalog` as a real filtering dimension inside it? A
same-scope catalog (`workspace`/`memories-workspace`, both workspace-rooted) sharing one
`WorkspaceIndexer` avoids proliferating owner locks and background threads per catalog, and lets
`klorb index scan` cover both for free — but only if `sqlite-vec`'s `vec0` KNN search can be
scoped to one catalog without a large catalog crowding out a small one's true nearest neighbor in
an unfiltered top-`k` search.

## Answer

One store per lifecycle scope, not one per catalog. `WorkspaceIndexer` now covers both the
`workspace` and `memories-workspace` catalogs in its single `workspace.db`, sharing its owner
lock, watcher, and thread pool; only `memories-global` — genuinely not workspace-rooted, since it
applies across every workspace — gets its own store via `MemoryCatalogIndexer`/
`get_global_memory_indexer()`.

`chunks_vec` declares `catalog TEXT PARTITION KEY`, and `chunks_fts` gets a plain `catalog
UNINDEXED` column; every `search_lexical`/`search_vector`/`hybrid_search` call takes a required
`catalog` argument. Verified empirically against the pinned `sqlite-vec==0.1.9` before committing
to this design: seeding 2000 unrelated vectors in one catalog and 3 real matches in another, an
unfiltered top-3 KNN query returned the noise catalog's vectors ahead of the real matches, while a
`catalog`-filtered query with the same `k` returned exactly the two true within-partition nearest
neighbors. FTS5's `MATCH` has no equivalent problem, since it doesn't truncate results before other
`WHERE` predicates apply.

## Reasoning

A separate store per catalog was the first design considered and initially implemented, since it
sidesteps the crowding-out question entirely by construction. It was reverted once the
partition-key test above confirmed a shared store is equally correct, and the operational costs it
avoided are real and compound with every catalog added (a third, `skills`-flavored catalog is
already anticipated): N independent owner locks and write locks for what's conceptually one
workspace's index; two klorb processes on the same workspace tree being able to independently
"own" different catalogs of the same tree, which has no coherent meaning; and `klorb index scan
--threads` having no sane interpretation across N independently-lifecycled indexers. Consolidating
onto one store per scope keeps those semantics singular while `catalog` still gives full query
isolation.
