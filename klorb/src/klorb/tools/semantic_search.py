# © Copyright 2026 Aaron Kimball
"""A Tool that searches the workspace's local hybrid (BM25 + vector KNN) search index for chunks
related to a natural-language query by meaning rather than exact wording."""

import fnmatch
import logging
from pathlib import Path
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import resolve_and_evaluate_read
from klorb.search_index.chunk import Chunk
from klorb.search_index.chunkers.base import CATALOG
from klorb.tools.exceptions import ToolCallError
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool
from klorb.tools.util import (
    SemanticSearchCore,
    coerce_queries_arg,
    get_or_create_secret_redactor,
    validate_queries,
)

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 25


class SemanticSearchTool(Tool):
    """Searches the workspace's local hybrid (BM25 + vector KNN) search index for chunks related
    to any of `queries` by meaning rather than exact wording, merging each query's hits
    (deduplicated by chunk, keeping the highest score) and ranking the union by fused relevance
    score.

    Each matched chunk is reported under its source file in `result["files"]` as
    `{"filename", "lines", "score"}`, `lines` a dense-format list with every line of the chunk's
    span marked `*`. A file with hits from more than one chunk gets one merged entry whose
    `score` is its best chunk's score.

    Raises `ToolCallError` if the workspace search index isn't available.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self._secret_redactor = get_or_create_secret_redactor(context.session)
        self._core = SemanticSearchCore(
            max_line_length=context.process_config.grep_max_line_length,
            secret_redactor=self._secret_redactor)

    def name(self) -> str:
        return "SemanticSearch"

    def category(self) -> str:
        return "FILES"

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Searches the workspace for code and documentation "
            "related to the given queries by meaning rather than literal match — useful "
            "when you don't know the exact string to search for. Use Grep for literal or regex. "
            "Optionally scope search to a file or directory "
            "with path (empty means whole project), or restrict files with "
            "a filename glob (e.g. '*.py'). Returns up to top_k chunk hits ranked "
            "by relevance, grouped under 'files' as {'filename', 'lines', 'score'}; "
            "each line is a string like '*42|matched text'. All lines marked with '*'. "
            "Number is the line number. Use outputStyle \"ListFiles\" to get "
            "just filenames. "
            "Lines with passwords have secrets "
            "replaced by `[[SECRET:<type>:<hash>]]` token. "
            "Copy token verbatim into EditFile to match or preserve that line."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "File or directory scope for search. Absolute or relative to project "
                        "root. Empty or null means whole project."
                    ),
                },
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": (
                        "One or more natural-language descriptions of what you're looking for. "
                        "Each is searched independently and the results are merged."
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "description": f"Maximum chunk hits to return. Defaults to {DEFAULT_TOP_K}.",
                },
                "file_glob": {
                    "type": "string",
                    "description": (
                        "Optional filename glob (e.g. '*.py') restricting files, "
                        "matched against file's bare name."
                    ),
                },
                "outputStyle": {
                    "type": "string",
                    "enum": ["ListFiles", "Matches"],
                    "description": (
                        "\"ListFiles\" returns filenames; \"Matches\" "
                        "(default) returns lines of matched chunks."
                    ),
                },
            },
            "required": ["queries"],
            "additionalProperties": False,
        }

    def _redact_strings(self, values: list[str]) -> list[str]:
        """Mask likely credentials out of each of `values` before it's returned."""
        return list(map(lambda value: self._secret_redactor.redact(self.context.session, value), values))

    def apply(self, args: dict[str, Any]) -> Any:
        search_path = args.get("path") or ""
        try:
            queries = validate_queries(coerce_queries_arg(args["queries"]))
        except KeyError:
            raise ValueError(
                "Missing required argument: 'queries'. Provide a non-empty array of search strings.")
        top_k = args.get("top_k", DEFAULT_TOP_K)
        file_glob = args.get("file_glob")
        output_style = args.get("outputStyle") or "Matches"
        if output_style not in ("ListFiles", "Matches"):
            raise ValueError('outputStyle must be "ListFiles" or "Matches"')
        logger.debug(
            "SemanticSearch %r in %r (file_glob=%s, top_k=%d, outputStyle=%s)",
            queries, search_path, file_glob, top_k, output_style)

        indexer = self.context.session.workspace_indexer if self.context.session is not None else None
        if indexer is None:
            raise ToolCallError(
                "The workspace search index isn't available for SemanticSearch (the workspace "
                "may be untrusted, the feature may be disabled, or `klorb init` may not have "
                "run).", category="business_logic")

        workspace_root = self.context.session_config.workspace.path.resolve()
        if search_path:
            resolved, verdict = resolve_and_evaluate_read(self.context, search_path)
            raise_if_not_allowed(
                verdict, resource_description=f"search {resolved}", path=resolved, is_write=False)
            if resolved.is_file():
                scope_path, scope_is_file = resolved, True
            elif resolved.is_dir():
                scope_path, scope_is_file = resolved, False
            else:
                raise FileNotFoundError(f"{search_path!r} does not exist")
        else:
            scope_path, scope_is_file = workspace_root, False

        search_queries = [
            self._secret_redactor.detokenize(self.context.session, query) for query in queries]

        hits = self._core.merged_hits([(indexer, CATALOG)], search_queries, limit_per_query=top_k * 4)

        in_scope = [
            (chunk, score) for chunk, score in hits
            if self._chunk_in_scope(chunk, workspace_root, scope_path, scope_is_file, file_glob)
        ]
        in_scope.sort(key=lambda pair: pair[1], reverse=True)
        truncated = len(in_scope) > top_k

        list_files: list[str] = []
        files: list[dict[str, Any]] = []
        files_by_name: dict[str, dict[str, Any]] = {}
        match_count = 0
        for chunk, score in in_scope[:top_k]:
            abs_path = workspace_root / chunk.source_path
            dense_lines = self._core.render_chunk_lines(self.context.session, abs_path, chunk)
            if dense_lines is None:
                continue
            filename = str(abs_path)
            match_count += len(dense_lines)
            if output_style == "ListFiles":
                if filename not in list_files:
                    list_files.append(filename)
                continue
            existing_entry = files_by_name.get(filename)
            if existing_entry is None:
                new_entry = {"filename": filename, "lines": dense_lines, "score": score}
                files.append(new_entry)
                files_by_name[filename] = new_entry
            else:
                existing_entry["lines"].extend(dense_lines)
                existing_entry["score"] = max(existing_entry["score"], score)

        logger.debug(
            "SemanticSearch found %d line(s) across %d file(s)",
            match_count, len(files) if output_style != "ListFiles" else len(list_files))

        return {
            "root": str(scope_path),
            "queries": self._redact_strings(queries),
            "file_glob": file_glob,
            "top_k": top_k,
            "files": list_files if output_style == "ListFiles" else files,
            "match_count": match_count,
            "truncated": truncated,
        }

    @staticmethod
    def _chunk_in_scope(
        chunk: Chunk, workspace_root: Path, scope_path: Path, scope_is_file: bool,
        file_glob: str | None,
    ) -> bool:
        abs_path = (workspace_root / chunk.source_path).resolve()
        if scope_is_file:
            if abs_path != scope_path:
                return False
        else:
            try:
                abs_path.relative_to(scope_path)
            except ValueError:
                return False
        return not file_glob or fnmatch.fnmatch(abs_path.name, file_glob)

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        queries = args.get("queries", "?")
        if error is not None:
            return f"SemanticSearch: {queries!r} failed: {error}"
        if not isinstance(result, dict):
            return f"SemanticSearch: {queries!r}"
        count = result.get("match_count", 0)
        suffix = "+" if result.get("truncated") else ""
        plural = "es" if count != 1 else ""
        root = result.get("root", args.get("path", "?"))
        return f"SemanticSearch: {queries!r} in {root} ({count}{suffix} match{plural})"

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Same as the default pretty-JSON rendering, but with `result["files"]` capped to its
        first 20 entries."""
        if error is not None or not isinstance(result, dict) or "files" not in result:
            return super().detail_view(args, result, error)
        files = result["files"]
        if len(files) <= 20:
            return super().detail_view(args, result, error)
        capped_result = dict(result)
        capped_result["files"] = files[:20]
        capped_result["files_omitted"] = len(files) - 20
        return super().detail_view(args, capped_result, error)
