# © Copyright 2026 Aaron Kimball
"""A Tool that searches every accessible memory file for lines containing any of several
literal search strings, also treating each file's own filename as a search subject, folding in
semantic hits from the memories catalogs' search indexes."""

import logging
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from klorb.search_index.catalogs import (
    MEMORIES_GLOBAL_CATALOG,
    MEMORIES_WORKSPACE_CATALOG,
    namespace_for_catalog,
)
from klorb.search_index.chunk import Chunk
from klorb.search_index.memory_indexer import get_global_memory_indexer
from klorb.search_index.ranking import DEFAULT_RRF_K
from klorb.tools.memory.common import Namespace, memory_namespace_dir, workspace_namespace_accessible
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool
from klorb.tools.util import (
    HybridSearchable,
    SemanticSearchCore,
    compile_queries,
    context_lines_for_matches,
    format_match_line,
    get_or_create_secret_redactor,
    match_line_indices,
    matches_only,
    validate_output_style,
    validate_queries,
)

logger = logging.getLogger(__name__)

ALL_NAMESPACES: tuple[Namespace, ...] = ("global", "workspace")
_VALID_NAMESPACE_FILTERS = frozenset({"global", "workspace", "all"})

SEMANTIC_TOP_K = 5
"""Cap on semantic hits folded into a result. A session isn't expected to accumulate many memory
files, so a handful of confident hits is enough."""

SEMANTIC_LIMIT_PER_QUERY = 10

SEMANTIC_MIN_SCORE = 1.0 / (DEFAULT_RRF_K + 3)
"""Minimum fused RRF score a semantic hit must clear to surface. Equivalent to ranking in the
top 3 of at least one of the lexical/vector lists, a high bar appropriate for a small memory
collection."""


def _validate_namespace_filter(raw: Any) -> str:
    if raw in (None, ""):
        return "all"
    if raw not in _VALID_NAMESPACE_FILTERS:
        raise ValueError(f'namespace must be "global", "workspace", or "all", got {raw!r}')
    return cast(str, raw)


def _semantic_hit_filename(namespace: str, chunk: Chunk) -> str:
    """A semantic hit's bare filename. A `memories-workspace` chunk's `source_path` is
    workspace-root-relative (`.klorb/memories/<filename>.md`); a `memories-global` chunk's is
    already bare."""
    return Path(chunk.source_path).name if namespace == "workspace" else chunk.source_path


class SearchMemoriesTool(Tool):
    """Searches memory files for lines containing any of `queries` as a literal, case-insensitive
    substring, plus a few high-confidence semantic hits when a memory search index is available.
    `namespace` optionally restricts the search to `global` or `workspace`; defaults to `all`.
    See docs/specs/memories.md.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self._core = SemanticSearchCore(
            max_line_length=context.process_config.grep_max_line_length,
            secret_redactor=get_or_create_secret_redactor(context.session))

    def name(self) -> str:
        return "SearchMemories"

    def aliases(self) -> Sequence[str]:
        return ("SearchMemory", "MemoriesSearch")

    def category(self) -> str:
        return "MEMORY"

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Searches memory files for lines containing any of the given search strings, each "
            "matched as a literal, case-insensitive substring (not a regular expression) -- "
            "equivalent to `grep -i -F -e 'seq1' -e 'seq2' ...` against every memory file's "
            "content -- plus a handful of high-confidence semantic hits by meaning rather than "
            "exact wording, when a memory search index is available. Results are grouped by "
            "file under 'results'; each entry's 'lines' are strings like '*42|matched text', "
            "where the leading '*' marks a matching line and the number is its 1-based line "
            "number; a semantic hit additionally carries a 'score'. A file's own filename is "
            "also searched: a query matching a filename returns that file even if none of its "
            "lines match, listing its first non-blank line. Optionally restrict the search to "
            "one namespace with namespace (\"global\" or \"workspace\"); defaults to \"all\", "
            "searching both. Use outputStyle to control the level of detail: \"ListFiles\" "
            "returns just deduplicated filenames as \"<namespace>/<filename>\"; \"Matches\" "
            "returns only the hit lines (default); \"FullContext\" returns hit lines plus "
            "surrounding context."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": (
                        "One or more literal search strings, matched case-insensitively (not "
                        "regular expressions) against both memory file content and filenames."
                    ),
                },
                "namespace": {
                    "type": "string",
                    "enum": ["global", "workspace", "all"],
                    "description": (
                        "Restrict the search to one namespace: \"global\" (applies to every "
                        "workspace) or \"workspace\" (this workspace only). Defaults to \"all\", "
                        "searching both."
                    ),
                },
                "outputStyle": {
                    "type": "string",
                    "description": (
                        "Controls the level of detail: \"ListFiles\" returns just deduplicated "
                        "filenames as \"<namespace>/filename\"; \"Matches\" returns only the "
                        "hit lines (default); \"FullContext\" returns hit lines plus surrounding "
                        "context."
                    ),
                },
            },
            "required": ["queries"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        try:
            queries = validate_queries(args["queries"])
        except KeyError:
            raise ValueError(
                "Missing required argument: 'queries'. Provide a non-empty array of search strings.")
        output_style = validate_output_style(args.get("outputStyle"))
        namespace_filter = _validate_namespace_filter(args.get("namespace"))
        namespaces = self._namespaces_to_search(namespace_filter)
        logger.debug(
            "SearchMemories %r (namespace=%s, outputStyle=%s)", queries, namespace_filter, output_style)

        compiled = compile_queries(queries, case_insensitive=True)

        context_lines = self.context.process_config.grep_context_lines

        if output_style == "ListFiles":
            list_files: list[str] = []
            for namespace in namespaces:
                list_files.extend(self._list_files_namespace(namespace, compiled))
            for hit_namespace, chunk, _score in self._semantic_hits(namespaces, queries):
                entry = f"{hit_namespace}/{_semantic_hit_filename(hit_namespace, chunk)}"
                if entry not in list_files:
                    list_files.append(entry)
            # Count matches (each filename string is one hit).
            match_count = len(list_files)
            logger.debug("SearchMemories found %d hit(s) across %d file(s)", match_count, len(list_files))
            return {
                "queries": queries,
                "match_count": match_count,
                "files": list_files,
            }

        results = []
        for namespace in namespaces:
            results.extend(self._search_namespace(namespace, compiled, output_style, context_lines))

        already_reported = {(result["namespace"], result["filename"]) for result in results}
        for hit_namespace, chunk, score in self._semantic_hits(namespaces, queries):
            filename = _semantic_hit_filename(hit_namespace, chunk)
            if (hit_namespace, filename) in already_reported:
                continue
            abs_path = self._resolve_semantic_hit_path(hit_namespace, chunk)
            lines = self._core.render_chunk_lines(self.context.session, abs_path, chunk)
            if lines is None:
                continue
            results.append({
                "namespace": hit_namespace, "filename": filename, "lines": lines, "score": score,
            })
            already_reported.add((hit_namespace, filename))

        # A content match contributes one hit per matching (`*`-prefixed) line; a filename-only
        # match, whose sole listed line is unmatched, still counts as a single hit.
        match_count = sum(
            starred if (starred := sum(1 for line in result["lines"] if line.startswith("*")))
            else 1
            for result in results
        )
        logger.debug("SearchMemories found %d hit(s) across %d file(s)", match_count, len(results))
        return {
            "queries": queries,
            "match_count": match_count,
            "results": results,
        }

    def _namespaces_to_search(self, namespace_filter: str) -> tuple[Namespace, ...]:
        candidates = ALL_NAMESPACES if namespace_filter == "all" else (cast(Namespace, namespace_filter),)
        return tuple(
            namespace for namespace in candidates
            if namespace == "global" or workspace_namespace_accessible(self.context))

    def _semantic_hits(
        self, namespaces: Sequence[Namespace], queries: list[str],
    ) -> list[tuple[str, Chunk, float]]:
        """Up to `SEMANTIC_TOP_K` `(namespace, chunk, score)` hits, scoring at least
        `SEMANTIC_MIN_SCORE`, from whichever of `namespaces`' memory catalog indexes are
        currently available. The `memories-global` catalog is additionally gated on
        `search_memories_index_enabled`; the `memories-workspace` catalog shares
        `session.workspace_indexer`, inheriting whatever gating left it `None`."""
        indexers: list[tuple[HybridSearchable, str]] = []
        if "global" in namespaces and self.context.session_config.search_memories_index_enabled:
            global_indexer = get_global_memory_indexer()
            if global_indexer is not None:
                indexers.append((global_indexer, MEMORIES_GLOBAL_CATALOG))
        if "workspace" in namespaces and self.context.session is not None:
            workspace_indexer = self.context.session.workspace_indexer
            if workspace_indexer is not None:
                indexers.append((workspace_indexer, MEMORIES_WORKSPACE_CATALOG))
        if not indexers:
            return []
        hits = self._core.merged_hits(
            indexers, queries, limit_per_query=SEMANTIC_LIMIT_PER_QUERY, min_score=SEMANTIC_MIN_SCORE)
        hits.sort(key=lambda pair: pair[1], reverse=True)
        return [
            (namespace_for_catalog(chunk.catalog), chunk, score) for chunk, score in hits[:SEMANTIC_TOP_K]
        ]

    def _resolve_semantic_hit_path(self, namespace: str, chunk: Chunk) -> Path:
        """The on-disk path a semantic hit's `chunk` was indexed from. A `memories-workspace`
        chunk's `source_path` is workspace-root-relative; a `memories-global` chunk's is a bare
        filename relative to the global memories directory."""
        if namespace == "workspace":
            return self.context.session_config.workspace.path.resolve() / chunk.source_path
        return memory_namespace_dir(self.context, cast(Namespace, namespace)) / chunk.source_path

    def _search_namespace(
        self, namespace: Namespace, compiled: re.Pattern[str],
        output_style: str = "Matches", context_lines: int = 0,
    ) -> list[dict[str, Any]]:
        namespace_dir = memory_namespace_dir(self.context, namespace)
        if not namespace_dir.is_dir():
            return []

        hits: list[dict[str, Any]] = []
        for path in sorted(namespace_dir.iterdir()):
            if not path.is_file() or path.suffix != ".md" or path.name.startswith("."):
                continue

            lines = path.read_text(encoding="utf-8").splitlines()
            matched_indices = match_line_indices(lines, compiled)
            if matched_indices:
                if output_style == "Matches":
                    line_data = matches_only(lines, matched_indices)
                else:  # FullContext
                    line_data = context_lines_for_matches(lines, matched_indices, context_lines)
                hits.append({
                    "namespace": namespace, "filename": path.name,
                    "lines": line_data,
                })
            elif compiled.search(path.name):
                first_non_blank = next(
                    ((index + 1, line) for index, line in enumerate(lines) if line.strip()),
                    (1, ""))
                hits.append({
                    "namespace": namespace, "filename": path.name,
                    "lines": [format_match_line(
                        first_non_blank[0], first_non_blank[1], matched=False)],
                })
        return hits

    def _list_files_namespace(
        self, namespace: Namespace, compiled: re.Pattern[str],
    ) -> list[str]:
        """Return deduplicated ``<namespace>/<filename>`` strings for every memory file
        whose content or filename matches any of the compiled queries."""
        namespace_dir = memory_namespace_dir(self.context, namespace)
        if not namespace_dir.is_dir():
            return []

        files: list[str] = []
        for path in sorted(namespace_dir.iterdir()):
            if not path.is_file() or path.suffix != ".md" or path.name.startswith("."):
                continue

            # Check filename first.
            if compiled.search(path.name):
                entry = f"{namespace}/{path.name}"
                if entry not in files:
                    files.append(entry)
                continue

            # Check content.
            lines = path.read_text(encoding="utf-8").splitlines()
            if match_line_indices(lines, compiled):
                entry = f"{namespace}/{path.name}"
                if entry not in files:
                    files.append(entry)

        return files

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        queries = args.get("queries", "?")
        if error is not None:
            return f"Search memories: {queries!r} failed: {error}"
        if not isinstance(result, dict):
            return f"Search memories: {queries!r}"
        count = result.get("match_count", 0)
        plural = "es" if count != 1 else ""
        return f"Search memories: {queries!r} ({count} match{plural})"
