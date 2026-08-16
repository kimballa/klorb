# Local search index

## Summary

`klorb.search_index` is a local, SQLite-backed hybrid (BM25 lexical + vector KNN) search index over
one workspace's filesystem — source code (Python, TypeScript/TSX) and markdown (`docs/`, ADRs,
specs, `TODO.md`, `CLAUDE.md`/`AGENTS.md`, READMEs). It backs `Grep`'s `search_mode="semantic"` path
(see `docs/adrs/00193-grep-search-mode-adds-semantic-hits-via-workspace-index.md`). Embeddings run
fully locally via a bundled ONNX model (`fastembed`); no network call is ever made at index or query
time. Tools/Skills/Memories catalogs are anticipated follow-up work reusing this same
chunk/embed/store/rank pipeline — not built yet.

## Chunking

`klorb.search_index.chunkers.router.ChunkerRouter` dispatches each file to a structural or
markdown chunker (by extension) plus the windowed chunker, unconditionally:

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
  blank-line-delimited paragraph. Shared verbatim by the anticipated Skills/Memories catalogs, since
  a skill/memory body is itself markdown.
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
the running Python was built, so relying on it isn't reliable) connection per workspace, at
`${workspace_root}/.klorb/index/workspace.db` (WAL mode):

* **`chunks`** — plain metadata table, one row per `Chunk`.
* **`chunks_fts`** — an FTS5 virtual table (`chunk_id UNINDEXED, body`); `search_lexical()` ranks by
  `bm25()`.
* **`chunks_vec`** — a `sqlite-vec` `vec0` virtual table (`chunk_id TEXT PRIMARY KEY,
  embedding FLOAT[384]`); `search_vector()` ranks by KNN distance.
* **`files`** — `source_path -> whole-file content_hash`, distinct from any individual chunk's own
  `content_hash`, so the indexer can answer "did this file change since it was last indexed"
  unambiguously without picking among a file's several chunks.
* **`meta`** — a `schema` row (`SCHEMA_NAME:SCHEMA_VERSION`); a mismatch drops and rebuilds every
  other table rather than migrating — the SQLite-file analogue of the JSON schema-envelope
  convention in `docs/specs/persisted-json-schema-versioning.md`, applied here since this isn't a
  JSON file.

`hybrid_search()` fuses `search_lexical()`/`search_vector()` (BM25 rank order and KNN rank order)
via `klorb.search_index.ranking.reciprocal_rank_fusion` — `score = Σ 1/(k + rank)` (`k=60`) across
whichever ranked lists a chunk appears in — which sidesteps having to calibrate BM25 scores against
cosine-distance scores on a common scale, then hydrates the top results into full `Chunk` rows.

Every write method (`upsert_chunks`, `delete_for_path`, `set_file_hash`) acquires a short-lived
`write.lock` (`klorb.lockfile.acquire_lockfile_with_backoff`) around its own transaction — defense in
depth around the owner-lock handoff race described below; read methods take no lock, relying on WAL
mode to see committed writes.

## Indexing: `WorkspaceIndexer`

`klorb.search_index.indexer.WorkspaceIndexer` owns one workspace's index end to end: the initial
scan, the background filesystem watcher, and cross-process ownership.

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
  an undecodable file). Compares each file's whole-content hash against `SearchIndexStore.
  file_hashes()` and only rechunks/re-embeds a file whose hash changed; a previously-indexed path no
  longer seen is deleted.
* **The watcher** — a `watchdog` `Observer` + debounce-`Timer`, the same idiom
  `klorb.hooks.fs_events.FileSystemWatcher`/`klorb.tui.workspace_file_index.WorkspaceFileIndex` use —
  reindexes (or deletes, if the path no longer exists or gitignore now excludes it) each changed path
  once a 1-second-quiet debounce window settles.

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
there's nothing to rebuild.

## `Grep` integration

See `docs/adrs/00193-grep-search-mode-adds-semantic-hits-via-workspace-index.md` for the full design
and rationale. In short: `Grep`'s `search_mode` parameter (`"literal"` | `"regex"` | `"semantic"`)
replaces the old `is_regex` boolean; `"semantic"` runs the literal search unchanged and merges in up
to `top_k` (default 10) chunk-level hits from `WorkspaceIndexer.hybrid_search()`, scoped by the same
`path`/`file_glob` the literal search obeys. Raises `ToolCallError` if `context.session.
workspace_indexer` is `None`.

## Configuration

* `sessionDefaults.search.workspaceIndex.enabled` — `bool`, default `true`, backing
  `SessionConfig.search_workspace_index_enabled`.

## Out of scope

* **Tools, Skills, Memories catalogs.** `chunk.py`/`embedding.py`/`store.py`/`ranking.py` are
  catalog-agnostic; each anticipated catalog needs only its own chunker (Skills/Memories reuse
  `chunkers.markdown.MarkdownChunker` directly) and a thin `Search*` integration. Not built yet.
* **A shared daemon.** Each klorb process opens the workspace's SQLite file directly; there is no
  subprocess/socket-RPC indexing service. Revisit if cross-process write contention on
  `.klorb/index/*.db` turns out to matter in practice — the owner-lock design already bounds it to at
  most one writer at a time, so this is a performance question, not a correctness one.
* **Precise interval-merging of a semantic chunk's line range into an existing literal-hit context
  window.** `Grep` appends a semantic chunk's dense lines after any literal hit's own lines rather
  than merging overlapping ranges — a token-efficiency imperfection, not a correctness bug.
* **Scoped-search efficiency at large scale.** `Grep`'s `path`/`file_glob` scoping for
  `search_mode="semantic"` is a post-filter over an over-fetched hybrid-search result, not a `WHERE`
  predicate pushed into the SQL query itself — fine for a single workspace's chunk count, but not
  necessarily efficient for a narrow scope inside a very large repository.
* **Reindexing on `.gitignore` edits.** Unlike `klorb.tui.workspace_file_index.WorkspaceFileIndex`,
  the watcher doesn't force a full rescan when a `.gitignore` file itself changes — a newly-ignored
  file already indexed stays indexed until some other event touches it.
