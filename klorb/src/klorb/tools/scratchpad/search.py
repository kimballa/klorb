# © Copyright 2026 Aaron Kimball
"""A Tool that searches the active session's scratchpad file for lines containing any of
several literal search strings, returning each match plus surrounding context lines."""

import logging
import re
from typing import Any

from klorb.tools.scratchpad.common import scratchpad_path
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)


class SearchScratchpadTool(Tool):
    """Searches the active session's scratchpad file for every line containing any of
    `queries` -- each matched as a literal, case-insensitive substring (never a regular
    expression), combined into one alternation for a single pass over the file (equivalent to
    `grep -i -F -e 'seq1' -e 'seq2' ...` against the scratchpad file) -- and returns each match
    together with `context_lines` (see `ProcessConfig.scratchpad_context_lines`) lines of
    surrounding content on each side. Overlapping or adjacent matches' context windows are
    merged into a single block, the same way `grep -C` collapses them, rather than reported as
    separate, redundantly-overlapping results.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self._context_lines = context.process_config.scratchpad_context_lines

    def name(self) -> str:
        return "SearchScratchpad"

    def description(self) -> str:
        return (
            "Searches your scratchpad for lines containing any of the given search strings, "
            "each matched as a literal, case-insensitive substring (not a regular expression) "
            "-- equivalent to `grep -i -F -e 'seq1' -e 'seq2' ...` against the scratchpad -- "
            f"and returns each match with {self._context_lines} lines of surrounding context "
            "on each side, merging overlapping/adjacent matches into a single block."
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
                        "regular expressions); a line containing any one of them is returned."
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
        logger.debug("SearchScratchpad %r", queries)

        combined_pattern = "|".join(re.escape(query) for query in queries)
        compiled = re.compile(combined_pattern, re.IGNORECASE)

        path = scratchpad_path(self.context)
        all_lines = path.read_text(encoding="utf-8").splitlines()
        total_lines = len(all_lines)

        matched_indices = [index for index, line in enumerate(all_lines) if compiled.search(line)]

        windows: list[list[int]] = []
        for index in matched_indices:
            start = max(0, index - self._context_lines)
            end = min(total_lines - 1, index + self._context_lines)
            if windows and start <= windows[-1][1] + 1:
                windows[-1][1] = max(windows[-1][1], end)
            else:
                windows.append([start, end])

        matched_index_set = set(matched_indices)
        blocks = [
            {
                "start_line": start + 1,
                "end_line": end + 1,
                "lines": [
                    {"line_number": i + 1, "line": all_lines[i], "matched": i in matched_index_set}
                    for i in range(start, end + 1)
                ],
            }
            for start, end in windows
        ]

        logger.debug(
            "SearchScratchpad found %d match(es) in %d block(s)", len(matched_indices), len(blocks))

        return {
            "queries": queries,
            "total_lines": total_lines,
            "match_count": len(matched_indices),
            "blocks": blocks,
        }

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        queries = args.get("queries", "?")
        if error is not None:
            return f"Search scratchpad: {queries!r} failed: {error}"
        if not isinstance(result, dict):
            return f"Search scratchpad: {queries!r}"
        count = result.get("match_count", 0)
        plural = "es" if count != 1 else ""
        return f"Search scratchpad: {queries!r} ({count} match{plural})"

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Same as the default pretty-JSON rendering, but with `result["blocks"]` capped to its
        first 12 entries -- a scratchpad with many scattered matches could otherwise dump far
        more context than useful to show inline.
        """
        if error is not None or not isinstance(result, dict) or "blocks" not in result:
            return super().detail_view(args, result, error)
        blocks = result["blocks"]
        if len(blocks) <= 12:
            return super().detail_view(args, result, error)
        capped_result = dict(result)
        capped_result["blocks"] = blocks[:12]
        capped_result["blocks_omitted"] = len(blocks) - 12
        return super().detail_view(args, capped_result, error)
