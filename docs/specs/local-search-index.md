# Local search index

## Summary

`klorb.search_index` is a local, SQLite-backed hybrid (BM25 lexical + vector KNN) search index over
several catalogs: the `workspace` catalog (source code, docs, and markdown across a trusted
workspace's filesystem, backing `SemanticSearch` — see
docs/adrs/00195-revert-grep-search-mode-semantic-search-becomes-its-own-tool.md) and the
`memories-global`/`memories-workspace` catalogs (each namespace's memory `.md` files, backing
`SearchMemories`'s semantic hits — see "Memories catalogs" below and docs/specs/memories.md).
Embeddings run fully locally via a bundled ONNX model (`fastembed`); no network call is ever made
at index or query time. A Skills catalog is anticipated follow-up work reusing this same
chunk/embed/store/rank pipeline — not built yet.

## Chunking

`klorb.search_index.chunkers.router.ChunkerRouter` dispatches each file to a structural or
markdown chunker (by extension) plus the windowed chunker, unconditionally. Every chunker's
`chunk()` (and `ChunkerRouter.chunk_file()`) takes a `catalog` argument, defaulting to the
`workspace` catalog, that's stamped onto every `Chunk` it produces -- the memories catalogs pass
their own catalog explicitly (see "Memories catalogs" below); nothing else about chunking differs
per catalog.

* **`.py`** — `chunkers.code_python.PythonChunker`: one chunk per class (a synthesized synopsis —
  header, docstring, field assignments, and method *signatures* only, no bodies), one chunk per
  method (full text), one chunk per top-level function (full text), one chunk per contiguous run of
  leftover top-level statements (imports, module-level assignments, ...).
* **`.ts`/`.tsx`** — `chunkers.code_typescript.TypeScriptChunker`/`TsxChunker`: the same shape via
  `chunkers._tree_sitter_base.TreeSitterChunker`, parameterized per language by a `LanguageSpec`
  rather than subclassed (the two grammars differ in node-type names; the walk is the same shape).
  `export`/`export default` wrappers are unwrapped for classification but the wrapper's own span
  (keyword included) is what gets chunked. `const`/`interface`/`type` declarations and arrow-function
  assignments get no special synopsis treatment — they fall into the leftover-statement grouping.
* **`.md`/`.markdown`** — `chunkers.markdown.MarkdownChunker`: one chunk per ATX-heading-delimited
  section; a section larger than `MAX_SECTION_TOKENS` (400) splits further into one chunk per
  blank-line-delimited paragraph. Shared verbatim by the memories catalogs, since a memory body is
  itself markdown, and by the anticipated Skills catalog.
* **Every file** (including ones with no structural/markdown chunker) — `chunkers.windowed.
  WindowedChunker`: a fixed-size (60 lines, 10-line overlap) sliding window over raw lines,
  independent of any structural boundary — a fallback recall net for content that doesn't parse
  cleanly or spans a structural boundary.

A file that fails to parse (tree-sitter reports no named nodes at all) contributes no structural
chunks; the windowed chunker still covers it. Every chunk is a `klorb.search_index.chunk.Chunk`
(`chunk_id`, `catalog`, `source_path`, `kind`, `start_line`/`end_line`, `text`, `token_count` — via
`klorb.token_estimate.estimate_tokens`, the same tokenizer klorb already uses for message-size
accounting, not the embedding model's own tokenizer — `content_hash`), built via `Chunk.create()` so
`chunk_id`/`token_count`/`content_hash` are always derived consistently rather than supplied
independently by each chunker.

## Embeddings

`klorb.search_index.embedding.EmbeddingModel` wraps a `fastembed.TextEmbedding` loaded from
`BAAI/bge-small-en-v1.5` (384 dimensions), bundled as klorb package data
(`klorb.resources/embedding-model/`, fetched once via `scripts/fetch_embedding_model.py` and
committed through Git LFS) rather than downloaded at runtime — Claude Code's own sandboxed/cloud
environments default to an egress allowlist of package-manager domains only (`pypi.org`,
`registry.npmjs.org`, `api.github.com`), so a first-run Hugging Face download would fail there. The
bundled tree is the `huggingface_hub` on-disk cache layout with every symlink dereferenced into a
real file (a wheel can't reliably ship symlinks), so `EmbeddingModel` loads it via `HF_HUB_OFFLINE=1`
plus `cache_dir=embedding_model_target_dir()` and never attempts network access.

`klorb.klorb_init.copy_embedding_model()` copies the packaged tree to
`embedding_model_target_dir()` (`$KLORB_DATA_DIR/embedding-model`) on every `klorb init`, the same
always-copy treatment `copy_tiktoken_cache()` gets. `embedding.embedding_model_available()` — a
cheap directory-existence check — gates every point that would otherwise construct a
`WorkspaceIndexer`/`SearchIndexStore`, so an environment that never ran `klorb init` (most unit
tests, a fresh CI container) never touches disk for this feature at all, let alone spawns a
background thread.

`EmbeddingModel.embed_passages()` (indexing) and `embed_query()` (search) use `fastembed`'s
document/query-side methods respectively, since `bge-small` is an asymmetric model with a query-side
instruction prefix. `onnxruntime`'s intra-op thread pool is capped at `EMBEDDING_THREADS` (2) rather
than left at its default (the machine's full physical core count) -- this model runs on
`WorkspaceIndexer`'s background thread, and an indexer that saturates every core to build its first
index is a poor definition of "background work."

## Storage and ranking

`klorb.search_index.store.SearchIndexStore` owns one `pysqlite3` (a self-contained SQLite build with
FTS5 and loadable extensions — the stdlib `sqlite3` module's extension-loading support varies by how
the running Python was built, so relying on it isn't reliable) connection to one database file (WAL
mode, `synchronous=NORMAL` — each commit's fsync is deferred to the next WAL checkpoint rather than
paid immediately, trading a small durability window (an OS crash or power loss, not an application
crash, can roll back the most recent transactions) for much cheaper per-file commits during a scan).
A single store can hold more than one catalog's chunks — `WorkspaceIndexer`'s own
`${workspace_root}/.klorb/index/workspace.db` holds both the `workspace` and `memories-workspace`
catalogs — since every query that matters at multi-catalog scale is scoped to one `catalog` (see
docs/adrs/00196-memories-catalogs-share-search-index-storage-scoped-by-catalog-partition-key.md):

* **`chunks`** — plain metadata table, one row per `Chunk`.
* **`chunks_fts`** — an FTS5 virtual table (`chunk_id UNINDEXED, catalog UNINDEXED, body`);
  `search_lexical()` ranks by `bm25()`, filtered to one `catalog` via a plain `catalog = ?`
  predicate alongside `MATCH`.
* **`chunks_vec`** — a `sqlite-vec` `vec0` virtual table (`chunk_id TEXT PRIMARY KEY, catalog TEXT
  PARTITION KEY, embedding FLOAT[384]`); `search_vector()` ranks by KNN distance, scoped to one
  `catalog` via `vec0`'s partition-key filtering — a true within-catalog nearest-neighbor search,
  not a global top-`limit` KNN result filtered afterward (which would let a large catalog crowd
  out a smaller one's real nearest neighbor).
* **`files`** — `source_path -> (content_hash, last_modified_ts)`; `content_hash` is a whole-file
  hash distinct from any individual chunk's own `content_hash`, so the indexer can answer "did this
  file change since it was last indexed" unambiguously without picking among a file's several
  chunks. `last_modified_ts` is the file's mtime as of that indexing, letting a scan skip reading
  and hashing a file whose mtime hasn't moved.
* **`meta`** — a `schema` row (`SCHEMA_NAME:SCHEMA_VERSION`); a mismatch drops and rebuilds every
  other table rather than migrating — the SQLite-file analogue of the JSON schema-envelope
  convention in `docs/specs/persisted-json-schema-versioning.md`, applied here since this isn't a
  JSON file.

`hybrid_search()` fuses `search_lexical()`/`search_vector()` (BM25 rank order and KNN rank order)
via `klorb.search_index.ranking.reciprocal_rank_fusion` — `score = Σ 1/(k + rank)` (`k=60`) across
whichever ranked lists a chunk appears in — which sidesteps having to calibrate BM25 scores against
cosine-distance scores on a common scale, then hydrates the top results into full `Chunk` rows.

Every write method (`upsert_chunks`, `delete_for_path`, `set_file_hash`) acquires a short-lived
`write.lock` (`klorb.lockfile.acquire_lockfile_with_backoff`) around its own transaction by default —
defense in depth around the owner-lock handoff race described below; read methods take no lock,
relying on WAL mode to see committed writes. A caller doing many writes in a row, such as the
initial scan, instead holds `write.lock` itself via `SearchIndexStore.acquire_write_lock()` and
passes it into each write call so the file lock is acquired once for the whole batch rather than
once per file.

## Indexing: `WorkspaceIndexer`

`klorb.search_index.indexer.WorkspaceIndexer` owns one workspace's index end to end: the initial
scan, the background filesystem watcher, and cross-process ownership, covering both the
`workspace` catalog (the recursive, gitignore-aware tree walk below) and the `memories-workspace`
catalog (`${workspace_root}/.klorb/memories/`, a flat `.md`-only directory the tree walk otherwise
skips as part of `.klorb`) — one indexer, one store, one owner lock, one thread pool for both.

* **Cross-process ownership.** A TUI session and a vscode-plugin ACP session both open on the same
  workspace is the normal case, not an edge case — two unrelated indexer threads racing to reindex
  the same files would thrash the index. Exactly one klorb process is the *owner* at a time: whichever
  acquires `${workspace_root}/.klorb/index/indexer.lock` first (`klorb.lockfile.create_lockfile().
  try_acquire()`, a one-shot attempt, not the backoff-retry helper) runs the watcher and performs all
  writes; every other process opens the same SQLite file read-only via `hybrid_search()` and starts no
  watcher of its own. If the owner exits, the OS releases its lock on fd close; the next process whose
  own `hybrid_search()` call finds the lock free claims ownership and runs a catch-up scan first
  (never trusting "the index must be current" just because a lock changed hands, since it may have
  missed filesystem events while it wasn't the owner).
* **`start()` is non-blocking** — it spawns a daemon thread that attempts ownership and, if
  successful, runs the initial scan and starts the watcher, so a klorb process's own startup (or its
  first `Grep` semantic call) is never blocked on a potentially slow first-time full-repo embed.
  Embedding a whole repository this way is a real, minutes-scale background cost on CPU-only
  hardware (hundreds of files, several chunks each) — `close()` signals and joins that thread (up
  to 30s) before touching the store, so an interactive session ending mid-scan doesn't race the
  scan's own store calls; the scan itself checks the same signal between files and returns early,
  leaving the index however far it got (the next owner's catch-up scan picks up the rest).
* **Initial scan** walks the workspace tree via `klorb.tools.util.gitignore.GitignoreFilter` (the
  same gitignore-aware filtering `klorb.tui.workspace_file_index.WorkspaceFileIndex` uses for its own
  `@`-mention index), skipping `.git`/`.klorb` unconditionally, symlinks, and any file over
  `MAX_INDEXED_FILE_BYTES` (500KB) or that fails to decode as UTF-8 (the same silent-skip `Grep` gives
  an undecodable file), then separately walks `.klorb/memories/` (flat, `.md`-only, no gitignore,
  no recursion) for the `memories-workspace` catalog. Both walks share one `existing`/`seen`
  bookkeeping pass so a file removed from either is cleaned up the same way. A file whose mtime
  matches its stored `FileIndexRecord.last_modified_ts` is skipped without being read or hashed.
  Otherwise its whole-content hash is compared against the stored `content_hash`: an unchanged hash
  (e.g. a `git checkout` that only bumps mtimes) just refreshes the stored mtime, while a changed
  hash rechunks and re-embeds it. A previously-indexed path no longer seen is deleted.
* **The watcher** — a `watchdog` `Observer` + debounce-`Timer`, the same idiom
  `klorb.hooks.fs_events.FileSystemWatcher`/`klorb.tui.workspace_file_index.WorkspaceFileIndex` use —
  is scheduled recursively on the whole workspace root, so it already receives events for
  `.klorb/memories/*` too; `_reindex_changed_path` special-cases a direct child of
  `.klorb/memories/` into the `memories-workspace` catalog before falling back to the ordinary
  `.klorb`-skip check for everything else under `.klorb`. Reindexes (or deletes, if the path no
  longer exists or gitignore now excludes it) each changed path once a 1-second-quiet debounce
  window settles.

A `workspace` catalog chunk's `Chunk.source_path` is workspace-root-relative
(`docs/README.md`); a `memories-workspace` chunk's is too (`.klorb/memories/notes.md`), since
both share `WorkspaceIndexer`'s own walk.

## The `memories-global` catalog: `MemoryCatalogIndexer`

`klorb.search_index.memory_indexer.MemoryCatalogIndexer` is the same shape as `WorkspaceIndexer`
(initial scan, background watcher, lockfile-based ownership) scoped to one flat, `.md`-only
directory instead of a whole recursive workspace tree — no gitignore filtering, no recursion, no
multi-threaded foreground scan. It backs only the `memories-global` catalog, indexing
`KLORB_DATA_DIR/memories/` (every workspace's shared global memories) into its own store at
`KLORB_DATA_DIR/index/memories-global.db`.

Global memories apply across every workspace, so this is a process-wide singleton
(`klorb.search_index.memory_indexer.get_global_memory_indexer()`, constructed and `start()`-ed
lazily on first call, like `get_embedding_model()`/`get_chunker_router()`) rather than a
per-`Session` object — one indexer covers every session in the process regardless of which
workspace it's rooted in. It runs independent of any workspace's trust: gated only on
`SessionConfig.search_memories_index_enabled` (checked by each tool call site, since the singleton
itself has no `Session` to read config from) and `embedding_model_available()`. Its `Chunk.
source_path` is a bare filename relative to `KLORB_DATA_DIR/memories/`, since that directory isn't
nested under any workspace root.

`namespace_for_catalog(catalog)` (`klorb.search_index.catalogs`) resolves a `Chunk.catalog` value
back to its memory namespace (`"global"`/`"workspace"`), used by `SearchMemories` to label a
semantic hit.

## Session integration

`Session` builds a `WorkspaceIndexer` once per **root session** (never per subagent — a second
`WorkspaceIndexer` in the same process would just lose the race for the same owner lock the root
session's instance already holds); every subagent shares it via its `parent` chain
(`SessionCoreMixin._create_workspace_indexer`, `Session.workspace_indexer` property). Construction
short-circuits to `None` — no directory created, no SQLite file opened, no thread spawned — unless
**all** of:

* `SessionConfig.search_workspace_index_enabled` is `true` (config key `search.workspaceIndex.enabled`,
  default `true`).
* `SessionConfig.workspace.trusted` is `true` — the same `.klorb`-writing trust gate `memories`/
  `skills` apply.
* `klorb.search_index.embedding.embedding_model_available()` is `true` — `klorb init` has run.

An autouse `klorb/tests/conftest.py` fixture (`_isolate_embedding_model_dir`) points
`embedding_model_target_dir()` at an empty per-test temp dir, so `embedding_model_available()` is
always `false` in the suite and the third gate above always short-circuits construction —
deterministic across machines whether or not `klorb init` has run locally. Tests covering the
feature itself construct or assign a `WorkspaceIndexer` directly instead of going through
`Session`.

`WorkspaceIndexer.close()` is registered as a root-session teardown subject
(`_WORKSPACE_INDEXER_TEARDOWN_SUBJECT`), added to `_INFRASTRUCTURE_TEARDOWN_SUBJECTS` so
`reset_session()`/`/clear` never tears it down mid-process — a `/clear` keeps the same workspace, so
there's nothing to rebuild. The `memories-global` catalog's indexer is a process-wide singleton
instead (`get_global_memory_indexer()`), so it isn't tied to any one session's teardown at all.

## `SemanticSearch`/`SearchMemories` integration

`klorb.tools.util.semantic_search_core.SemanticSearchCore` is the mechanic both tools share:
`merged_hits()` runs a list of queries through one or more `(HybridSearchable, catalog)` pairs
(anything with a `hybrid_search(query_text, limit, catalog)` method — `WorkspaceIndexer` and
`MemoryCatalogIndexer` both qualify), deduplicating by chunk id and keeping each chunk's highest
score, with an optional minimum-score floor; `render_chunk_lines()` re-reads a chunk's source file
fresh and renders its line span in the shared dense-line format, secret-redacted and truncated to
the caller's line length.

See `docs/adrs/00195-revert-grep-search-mode-semantic-search-becomes-its-own-tool.md` for
`SemanticSearch`'s full design and rationale. In short: `klorb.tools.semantic_search.
SemanticSearchTool` calls `merged_hits()` against `[(context.session.workspace_indexer,
CATALOG)]` and returns up to `top_k` (default 25) chunk-level hits scoped by the same
`path`/`file_glob` `Grep` uses for its own walk. Raises `ToolCallError` if
`context.session.workspace_indexer` is `None`.

`klorb.tools.memory.search_memories.SearchMemoriesTool` calls `merged_hits()` against whichever of
`(get_global_memory_indexer(), MEMORIES_GLOBAL_CATALOG)`/`(context.session.workspace_indexer,
MEMORIES_WORKSPACE_CATALOG)` are available for the requested `namespace` (see
docs/specs/memories.md), with a minimum-score floor (`SEMANTIC_MIN_SCORE`, equivalent to ranking in
the top 3 of at least one of the lexical/vector lists) and a small cap (`SEMANTIC_TOP_K`, 5)
appropriate for a collection that isn't expected to hold many memory files. Unlike
`SemanticSearch`, an unavailable index is never an error: the hits it would have added are simply
omitted, since `SearchMemories`'s literal keyword search always still runs. A semantic hit's
on-disk path is resolved per namespace — workspace-root-relative for `workspace`, relative to
`KLORB_DATA_DIR/memories/` for `global` — matching each catalog's own `Chunk.source_path`
convention.

## CLI: `klorb index`

`klorb index <action>` gives a human direct command-line access to the index, independent of any
agent session. `klorb.cli.index.run_index_cli` is a thin dispatcher (by `argv[0]`) to the actual
sub-main functions in `klorb.search_index.cli`:

* **`search <query>`** (`-k`/`--limit`, default `DEFAULT_SEARCH_LIMIT`; `--json`) — runs
  `WorkspaceIndexer.hybrid_search()` against the `workspace` catalog only and prints the results
  grouped by file, in the same dense-line shape (`*line|text`, one entry per file with `score`)
  `SemanticSearch` returns. `--json` emits that shape directly; otherwise each file's block is
  pretty-printed with its score. If no process currently owns the workspace's index, this claims
  ownership and runs a full scan synchronously first, per `hybrid_search()`'s own contract.
* **`scan`** (`-j`/`--threads`, default `os.cpu_count()`; `--rebuild`) — calls
  `WorkspaceIndexer.run_foreground_scan()`, a synchronous counterpart to `start()`'s background
  scan: it walks the workspace tree and `.klorb/memories/` once, (re)indexes every dirty file in
  either the `workspace` or `memories-workspace` catalog, and returns before exiting rather than
  continuing on a background thread. `--rebuild` clears the store first (`SearchIndexStore.
  clear()`) so every file (both catalogs) is treated as dirty. Multi-threaded scanning fans the
  per-file read/chunk/embed work — across both catalogs together — across `num_threads` worker
  threads:
  * Each `TreeSitterChunker` keeps a lazily-constructed `Parser` per thread (`threading.local()`)
    rather than one shared instance, since a `Parser` isn't safe for concurrent use, so chunking
    genuinely parallelizes rather than serializing behind a lock.
  * Each worker thread also gets its own `EmbeddingModel(threads=1)` (also `threading.local()`,
    built once per thread and reused for every file that thread processes) instead of sharing
    `get_embedding_model()`'s cached singleton. That singleton's session is capped at
    `EMBEDDING_THREADS` (2) for the *background* scan's sake (see "Embeddings" above); every
    worker thread funneling through the same capped session would bottleneck there regardless of
    `num_threads`, since onnxruntime doesn't grow a session's own thread pool to serve concurrent
    `Run()` calls faster. A single-threaded scan (`num_threads=1`, the default when `-j 1`) keeps
    using the shared singleton instead, since there's no contention to avoid.
  A `KeyboardInterrupt` mid-scan drops every not-yet-started file immediately instead of draining
  the whole backlog, so it only waits on the files already in flight (up to `num_threads`) before
  returning — see `run_foreground_scan`'s own docstring. Raises if another process already owns
  the index (that process's own watcher already keeps it current) or the embedding model isn't
  installed. `_scan_dirty_files` accumulates each phase's total wall-clock time across every file
  and worker thread (`_ScanPhaseTimings`: read/chunk/embed/store) and logs the breakdown at debug
  level alongside its usual scan-summary line, so a slow scan's dominant cost is visible directly
  (`KLORB_LOG_LEVEL=DEBUG klorb index scan ...`) instead of inferred from CPU usage.
* **`stats`** (`--json`) — reads `SearchIndexStore.stats()` (file/chunk counts, chunk counts by
  `kind`, on-disk size including WAL/SHM sidecars) without constructing a `WorkspaceIndexer`, so it
  never creates an index that doesn't already exist.

All three actions resolve the workspace root via `TrustManager().resolve_workspace(cwd)` but,
unlike `Session`'s own gate (see "Session integration" above), don't check `workspace.trusted` —
an explicit `klorb index` invocation is itself the user's authorization, the same treatment
`klorb init` gets. `klorb index` operates on `WorkspaceIndexer` (the `workspace` and
`memories-workspace` catalogs); it has no equivalent action for the `memories-global` catalog's
`MemoryCatalogIndexer`.

## Configuration

* `sessionDefaults.search.workspaceIndex.enabled` — `bool`, default `true`, backing
  `SessionConfig.search_workspace_index_enabled`. Also gates the `memories-workspace` catalog,
  since it shares `WorkspaceIndexer` with `workspace`.
* `sessionDefaults.search.memoriesIndex.enabled` — `bool`, default `true`, backing
  `SessionConfig.search_memories_index_enabled`. Governs only the `memories-global` catalog.

## Out of scope

* **A Tools/Skills catalog.** `chunk.py`/`embedding.py`/`store.py`/`ranking.py` are
  catalog-agnostic; each anticipated catalog needs only its own chunker (Skills reuses
  `chunkers.markdown.MarkdownChunker` directly, like the memories catalogs already do) and a thin
  `Search*` integration. Not built yet.
* **A `klorb index` equivalent for the `memories-global` catalog.** There's no CLI access to
  `MemoryCatalogIndexer`'s `search`/`scan`/`stats` independent of an agent session.
* **A shared daemon.** Each klorb process opens the workspace's SQLite file directly; there is no
  subprocess/socket-RPC indexing service. Revisit if cross-process write contention on
  `.klorb/index/*.db` turns out to matter in practice — the owner-lock design already bounds it to at
  most one writer at a time, so this is a performance question, not a correctness one.
* **Scoped-search efficiency at large scale.** `SemanticSearch`'s `path`/`file_glob` scoping is a
  post-filter over an over-fetched hybrid-search result, not a `WHERE` predicate pushed into the
  SQL query itself — fine for a single workspace's chunk count, but not necessarily efficient for
  a narrow scope inside a very large repository.
* **Reindexing on `.gitignore` edits.** Unlike `klorb.tui.workspace_file_index.WorkspaceFileIndex`,
  the watcher doesn't force a full rescan when a `.gitignore` file itself changes — a newly-ignored
  file already indexed stays indexed until some other event touches it.
