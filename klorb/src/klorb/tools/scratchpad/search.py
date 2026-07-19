# © Copyright 2026 Aaron Kimball
"""A Tool that searches the active session's scratchpad file for lines containing any of
several literal search strings, returning each match plus surrounding context lines."""

import logging
from typing import Any

from klorb.tools.scratchpad.common import scratchpad_path
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool
from klorb.tools.util import (
    compile_queries,
    context_lines_for_matches,
    match_line_indices,
    matches_only,
    validate_output_style,
    validate_queries,
)

logger = logging.getLogger(__name__)


class SearchScratchpadTool(Tool):
    """Searches the active session's scratchpad file for every line containing any of
    `queries` -- each matched as a literal, case-insensitive substring (never a regular
    expression), combined into one alternation for a single pass over the file (equivalent to
    `grep -i -F -e 'seq1' -e 'seq2' ...` against the scratchpad file) -- and returns each match
    together with `context_lines` (see `ProcessConfig.scratchpad_context_lines`) lines of
    surrounding content on each side. Overlapping or adjacent matches' context windows are
    merged, the same way `grep -C` collapses them, rather than reported as separate,
    redundantly-overlapping results.

    `result["lines"]` is a flat list of dense-format strings shared with `GrepTool` (see
    `klorb.tools.util.search_core`): a leading `*` marks a matching line, and each line carries
    its own 1-based number, so a break between two merged context windows shows up as a jump in
    those numbers rather than a separate block wrapper.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self._context_lines = context.process_config.scratchpad_context_lines

    def name(self) -> str:
        return "SearchScratchpad"

    def category(self) -> str:
        return "SCRATCHPAD"

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Searches your scratchpad for lines containing any of the given search strings, "
            "each matched as a literal, case-insensitive substring (not a regular expression) "
            "-- equivalent to `grep -i -F -e 'seq1' -e 'seq2' ...` against the scratchpad -- "
            f"and returns each match with {self._context_lines} lines of surrounding context "
            "on each side, merging overlapping/adjacent matches. Each entry in 'lines' is a "
            "string like '*42|matched text' or ' 41|context text', where the leading '*' marks "
            "a matching line and the number is its 1-based line number (a gap in those numbers "
            "marks a break between context windows). Use outputStyle to control the level "
            "of detail: \"Matches\" returns only the hit lines (default); \"FullContext\" "
            "returns hit lines plus surrounding context. \"ListFiles\" is not supported."
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
                "outputStyle": {
                    "type": "string",
                    "description": (
                        "Controls the level of detail: \"Matches\" returns only the hit lines "
                        "(default); \"FullContext\" returns hit lines plus surrounding context."
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
        output_style = validate_output_style(args.get("outputStyle"), allow_list_files=False)
        logger.debug("SearchScratchpad %r (outputStyle=%s)", queries, output_style)

        compiled = compile_queries(queries, case_insensitive=True)

        path = scratchpad_path(self.context)
        all_lines = path.read_text(encoding="utf-8").splitlines()
        total_lines = len(all_lines)

        matched_indices = match_line_indices(all_lines, compiled)
        if output_style == "Matches":
            lines = matches_only(all_lines, matched_indices)
        else:  # FullContext
            lines = context_lines_for_matches(all_lines, matched_indices, self._context_lines)

        logger.debug(
            "SearchScratchpad found %d match(es) in %d line(s)", len(matched_indices), len(lines))

        return {
            "queries": queries,
            "total_lines": total_lines,
            "match_count": len(matched_indices),
            "lines": lines,
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
        """Same as the default pretty-JSON rendering, but with `result["lines"]` capped to its
        first 60 entries -- a scratchpad with many scattered matches could otherwise dump far
        more context than useful to show inline.
        """
        if error is not None or not isinstance(result, dict) or "lines" not in result:
            return super().detail_view(args, result, error)
        lines = result["lines"]
        if len(lines) <= 60:
            return super().detail_view(args, result, error)
        capped_result = dict(result)
        capped_result["lines"] = lines[:60]
        capped_result["lines_omitted"] = len(lines) - 60
        return super().detail_view(args, capped_result, error)
