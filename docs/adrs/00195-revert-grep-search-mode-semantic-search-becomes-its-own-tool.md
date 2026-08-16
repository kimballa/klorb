# Revert Grep's `search_mode`; semantic search becomes its own `SemanticSearch` tool

* Date: 2026-08-16

## Question

`docs/adrs/00193-grep-search-mode-adds-semantic-hits-via-workspace-index.md` folded the workspace
search index's hybrid (BM25 + vector KNN) search into `Grep` itself, replacing `is_regex: bool`
with `search_mode: "literal" | "regex" | "semantic"` and merging semantic chunk hits into `Grep`'s
existing `files: [{filename, lines}]` result. Should `Grep` keep that shape, or should semantic
search move back out into a distinct tool with `Grep` reverted to its original interface?

## Answer

Revert `GrepTool` to its pre-00193 form: `is_regex: bool`, no `search_mode`, no `top_k`, no
semantic merge path. Add `klorb.tools.semantic_search.SemanticSearchTool` (name `SemanticSearch`)
as a standalone tool with an args schema mirroring `Grep`'s (`path`, `queries`, `file_glob`,
`outputStyle`) plus `top_k` to bound how many chunk hits come back, backed directly by
`WorkspaceIndexer.hybrid_search()`. `Grep` no longer references the workspace search index at all.

## Reasoning

* **A dedicated tool name and description resolve the ambiguity 00193 was worried about.** 00193
  rejected a second tool on the grounds that the model would have to choose between two
  overlapping "find this in the codebase" tools on every call. In practice, `Grep`'s description
  and `SemanticSearch`'s description name the axis directly (exact text vs. meaning), which is a
  clearer signal than a `search_mode` enum value buried in one tool's parameters — a model already
  has to pick a search strategy either way, and a distinct tool makes that choice explicit rather
  than implicit in an argument.
* **The shared machinery 00193 worried about duplicating already lives in reusable helpers,
  not in `GrepTool` itself.** `klorb.tools.util.search_core` (`validate_queries`,
  `format_match_line`, ...) is already factored out and used by `Grep`, `SearchScratchpad`, and
  `SearchMemories`; `SemanticSearchTool` reuses the same helpers rather than duplicating them.
* **Simpler contracts for both tools.** `Grep`'s result shape, description, and parameter schema
  go back to describing one thing (exact literal/regex matching) instead of two. `SemanticSearch`
  owns every workspace-index-specific concern (`top_k`, the `ToolCallError`-when-unavailable
  contract) without those concerns leaking into `Grep`'s otherwise-unrelated interface.

### What's unchanged from 00193

The index itself (`klorb.search_index`, see `docs/specs/local-search-index.md`), the
dense-line rendering convention (every line of a matched chunk marked `*`), the
re-read-the-file-fresh-rather-than-reuse-`Chunk.text` behavior, and the
`ToolCallError`-when-the-index-is-unavailable contract all carry over unchanged into
`SemanticSearchTool` — only the tool surface they're exposed through moved.
