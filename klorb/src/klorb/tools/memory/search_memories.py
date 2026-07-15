# © Copyright 2026 Aaron Kimball
"""A Tool that searches every accessible memory file for lines containing any of several
literal search strings, also treating each file's own filename as a search subject."""

import logging
import re
from typing import Any, cast

from klorb.permissions.table import raise_if_not_allowed
from klorb.tools.memory.common import Namespace, memory_namespace_dir, workspace_namespace_accessible
from klorb.tools.tool import Tool
from klorb.tools.util import (
    compile_queries,
    context_lines_for_matches,
    format_match_line,
    match_line_indices,
    matches_only,
    validate_output_style,
    validate_queries,
)

logger = logging.getLogger(__name__)


class SearchMemoriesTool(Tool):
    """Searches every memory file in both the `global` and `workspace` namespaces (there is no
    `namespace` argument to narrow this -- like `ListMemories`, it always covers every
    accessible namespace) for lines containing any of `queries` -- each matched as a literal,
    case-insensitive substring (never a regular expression), the same
    `klorb.tools.util.search_core` construction `GrepTool` and `SearchScratchpadTool` use.

    Each matching file is reported once in `result["results"]` as `{"namespace", "filename",
    "lines"}`, where `lines` is a flat list of dense-format strings shared with `GrepTool` (a
    leading `*` marks a matching line; each line carries its own 1-based number). There is no
    surrounding context -- only the matching lines themselves are listed.

    A file's own `filename` is also a search subject: when `filename` matches, the file is
    reported as a hit even if none of its lines do, listing its first non-blank line (as an
    unmatched ` `-prefixed line, since the content itself didn't match) so a query like "bird"
    finds a file named `you-like-birds.md` by name alone. A file matched by both its filename
    and its content is reported once, using its real content matches, never a duplicate
    filename-only entry.

    The `workspace` namespace is skipped entirely (not just filtered out of results) whenever
    the current workspace is untrusted. Gated by `tools.memory.readPermission`
    (`ProcessConfig.memory_read_permission`), the same flag that governs `ListMemories` and
    `ReadMemory`.
    """

    def name(self) -> str:
        return "SearchMemories"

    def description(self) -> str:
        return (
            "Searches every memory file (both the global and workspace namespaces) for lines "
            "containing any of the given search strings, each matched as a literal, "
            "case-insensitive substring (not a regular expression) -- equivalent to "
            "`grep -i -F -e 'seq1' -e 'seq2' ...` against every memory file's content. Results "
            "are grouped by file under 'results'; each entry's 'lines' are strings like "
            "'*42|matched text', where the leading '*' marks a matching line and the number is "
            "its 1-based line number. A file's own filename is also searched: a query matching "
            "a filename returns that file even if none of its lines match, listing its first "
            "non-blank line. Use outputStyle to control the level of detail: \"ListFiles\" "
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
        logger.debug("SearchMemories %r (outputStyle=%s)", queries, output_style)

        raise_if_not_allowed(
            self.context.process_config.memory_read_permission,
            resource_description="search memories")

        compiled = compile_queries(queries, case_insensitive=True)

        context_lines = self.context.process_config.grep_context_lines

        if output_style == "ListFiles":
            list_files: list[str] = []
            results: list[dict[str, Any]] = []
            for namespace in cast(tuple[Namespace, ...], ("global", "workspace")):
                if namespace == "workspace" and not workspace_namespace_accessible(self.context):
                    continue
                list_files.extend(self._list_files_namespace(namespace, compiled))
            # Count matches (each filename string is one hit).
            match_count = len(list_files)
            logger.debug("SearchMemories found %d hit(s) across %d file(s)", match_count, len(list_files))
            return {
                "queries": queries,
                "match_count": match_count,
                "files": list_files,
            }

        results = []
        for namespace in cast(tuple[Namespace, ...], ("global", "workspace")):
            if namespace == "workspace" and not workspace_namespace_accessible(self.context):
                continue
            results.extend(self._search_namespace(namespace, compiled, output_style, context_lines))

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
