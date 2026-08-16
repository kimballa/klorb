> Superseded by
> `docs/adrs/00195-revert-grep-search-mode-semantic-search-becomes-its-own-tool.md`: semantic
> search moved back out into its own `SemanticSearch` tool, and `Grep` reverted to its plain
> `is_regex: bool` interface.

# Grep gains a `search_mode="semantic"` path backed by the workspace search index

* Date: 2026-08-15

## Question

klorb's line-search tools (`Grep`, `SearchScratchpad`, `SearchMemories`) are all literal-substring/
regex matching — `docs/specs/skills.md`'s own "Out of scope" section flags "vector-indexed skill
search" as unbuilt follow-up work. Adding a local, SQLite-backed hybrid (BM25 + vector KNN) search
index for the workspace filesystem catalog (`klorb.search_index`, see
docs/specs/local-search-index.md) raises a tool-surface question `docs/adrs/00117-grep-search-tools-
share-dense-line-core.md` didn't anticipate: should this new capability be a new tool (`SearchCode`),
or a mode of the existing `Grep`? And however it's exposed, how does a fundamentally different
result shape (scored, chunk-level hits rather than exact line matches) fit `Grep`'s existing
`files: [{filename, lines}]` dense-line contract without breaking it?

## Answer

Extend `GrepTool` itself rather than adding a new tool. `is_regex: bool` becomes
`search_mode: "literal" | "regex" | "semantic"` — `"literal"` is the previous default-`false`
behavior, `"regex"` the previous `is_regex=true` behavior, both otherwise unchanged.
`search_mode="semantic"` runs the same literal-substring search as `"literal"` (never replaces it)
and additionally merges in up to `top_k` chunk-level hits from `klorb.search_index`'s hybrid search,
scoped by the same `path`/`file_glob` the literal search already obeys.

The wire shape stays `files: [{filename, lines}]`; two new optional per-entry fields, populated only
when a semantic hit touched that file, carry the extra information:

* `match_kind` — `"literal"`, `"semantic"`, or `"literal+semantic"`.
* `score` — the fused RRF score from the semantic hit (`klorb.search_index.ranking.
  reciprocal_rank_fusion`).

A file whose only hit is a semantic chunk (no literal substring match in it) renders that chunk's
`start_line`..`end_line` span in the existing dense-line format (`format_match_line`), but with
**every** line in the span marked `*` — extending the existing contract ("`*` means this line
matched a query") to mean "this line is part of a chunk that matched a semantic query," since a whole
chunk, not one exact line, is the unit of semantic relevance. A file matched by both a literal hit
and a semantic hit gets both: the literal lines as before, with the semantic chunk's lines appended
(not interval-merged with the literal windows — see "Rejected alternatives").

`search_mode="semantic"` raises `ToolCallError` if `context.session.workspace_indexer` is `None` —
untrusted workspace, feature disabled, or `klorb init` never run — rather than silently falling back
to literal-only results with no explanation.

## Reasoning

* **One tool, not two.** `Grep` is already the model's "find this in the codebase" tool; a second
  `SearchCode` tool competing for the same intent would force the model to choose between them on
  every call, and `is_regex`→`search_mode` is a strict superset of the same "how do I interpret
  `queries`" axis the tool already exposes — `"semantic"` is a natural third value, not a new concept.
* **Semantic mode never replaces the literal search.** A model calling `Grep` already expects an
  exact-substring safety net; dropping it in `"semantic"` mode would make the tool's behavior depend
  on a mode flag in a way that's easy to get wrong (e.g. searching for a literal identifier that also
  happens to have poor semantic recall). Running both and merging is strictly more capable.
* **Reusing `files`/dense-lines instead of a new result shape** keeps every existing consumer
  (`detail_view`'s 20-file cap, the spill mechanism, `_parse_lines`-style test/UI parsing) working
  unchanged for `"literal"`/`"regex"` calls, and lets a `"semantic"` call's *literal* half look
  exactly like an ordinary `Grep` result — only the two new optional fields are semantic-specific.
* **Marking every line of a semantic-only chunk, not just one line,** is the simplest faithful
  rendering of "the whole chunk matched a concept" without inventing a third line-marker symbol
  (which `docs/adrs/00117` doesn't reserve, and every existing dense-line parser doesn't expect).
* **Re-reading the file for display rather than reusing `Chunk.text`** matters because a structural
  chunk's `text` can be a synthesized synopsis (a class's field/method-signature summary — see
  `klorb.search_index.chunkers._tree_sitter_base`), not the file's literal content over
  `start_line`..`end_line`. Dense-line rendering must show real file lines with real line numbers, so
  `GrepTool._render_chunk_lines` reads `abs_path` fresh instead.

### Rejected alternatives

* **A separate `SearchCode` tool.** Rejected: splits one intent ("find this in the codebase") across
  two tools with overlapping but not identical result shapes, forcing the model to guess which one to
  call, and duplicates `Grep`'s existing scoping/spill/redaction machinery for no real benefit.
* **Precisely interval-merging a semantic chunk's line range with a file's existing literal context
  windows** (so overlapping ranges produce one clean merged window, mirroring
  `context_lines_for_matches`'s own window-merge logic). Rejected for the MVP: appending the semantic
  chunk's dense lines after the literal ones is simpler to implement and reason about, at the cost of
  occasional duplicate/overlapping lines in a result — a token-efficiency imperfection, not a
  correctness bug. Precise merging is reasonable follow-up work if it turns out to matter in practice.
* **Silently degrading to literal-only results when the workspace index is unavailable.** Rejected:
  the model would have no way to know its semantic query wasn't actually run, and might draw false
  "nothing found" conclusions. An explicit `ToolCallError` telling it to retry with `"literal"`/
  `"regex"` is more honest and actionable.
