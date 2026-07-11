# © Copyright 2026 Aaron Kimball
"""A Tool that searches every accessible memory file for lines containing any of several
literal search strings, also treating each file's own filename as a search subject."""

import logging
import re
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.tools.memory.common import Namespace, memory_namespace_dir, workspace_namespace_accessible
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)


class SearchMemoriesTool(Tool):
    """Searches every memory file in both the `global` and `workspace` namespaces (there is no
    `namespace` argument to narrow this -- like `ListMemories`, it always covers every
    accessible namespace) for lines containing any of `queries` -- each matched as a literal,
    case-insensitive substring (never a regular expression), the same `re.escape`-and-alternate
    construction `SearchScratchpadTool` uses.

    A file's own `filename` is also a search subject: when `filename` matches, the file is
    reported as a hit even if none of its lines do, using its first non-blank line as the
    reported `line` (regardless of whether that line itself matches) -- so a query like "bird"
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
            "`grep -i -F -e 'seq1' -e 'seq2' ...` against every memory file's content. A "
            "file's own filename is also searched: a query matching a filename returns that "
            "file even if none of its lines match, using its first non-blank line as the "
            "reported match."
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
            },
            "required": ["queries"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        queries = args["queries"]
        if not isinstance(queries, list) or not queries:
            raise ValueError("queries must be a non-empty array of search sequences")
        for query in queries:
            if not isinstance(query, str):
                raise ValueError(f"each entry in queries must be a string, got {query!r}")
        logger.debug("SearchMemories %r", queries)

        raise_if_not_allowed(
            self.context.process_config.memory_read_permission,
            resource_description="search memories")

        combined_pattern = "|".join(re.escape(query) for query in queries)
        compiled = re.compile(combined_pattern, re.IGNORECASE)

        results: list[dict[str, Any]] = []
        for namespace in ("global", "workspace"):
            if namespace == "workspace" and not workspace_namespace_accessible(self.context):
                continue
            results.extend(self._search_namespace(namespace, compiled))

        logger.debug("SearchMemories found %d hit(s)", len(results))
        return {
            "queries": queries,
            "match_count": len(results),
            "results": results,
        }

    def _search_namespace(self, namespace: Namespace, compiled: re.Pattern[str]) -> list[dict[str, Any]]:
        namespace_dir = memory_namespace_dir(self.context, namespace)
        if not namespace_dir.is_dir():
            return []

        hits: list[dict[str, Any]] = []
        for path in sorted(namespace_dir.iterdir()):
            if not path.is_file() or path.suffix != ".md" or path.name.startswith("."):
                continue

            lines = path.read_text(encoding="utf-8").splitlines()
            line_hits = [
                {"line_number": index + 1, "line": line}
                for index, line in enumerate(lines) if compiled.search(line)
            ]
            if line_hits:
                for hit in line_hits:
                    hits.append({"namespace": namespace, "filename": path.name, **hit})
            elif compiled.search(path.name):
                first_non_blank = next(
                    ((index + 1, line) for index, line in enumerate(lines) if line.strip()),
                    (1, ""))
                hits.append({
                    "namespace": namespace, "filename": path.name,
                    "line_number": first_non_blank[0], "line": first_non_blank[1],
                })
        return hits

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        queries = args.get("queries", "?")
        if error is not None:
            return f"Search memories: {queries!r} failed: {error}"
        if not isinstance(result, dict):
            return f"Search memories: {queries!r}"
        count = result.get("match_count", 0)
        plural = "es" if count != 1 else ""
        return f"Search memories: {queries!r} ({count} match{plural})"
