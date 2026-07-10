# © Copyright 2026 Aaron Kimball
"""A Tool that reads a range of lines from the active session's scratchpad file for a model."""

import logging
from typing import Any

from klorb.tools.scratchpad.common import scratchpad_path
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool, truncate_lines

logger = logging.getLogger(__name__)


class ReadScratchpadTool(Tool):
    """Reads up to `max_lines` lines from the active session's scratchpad file (see
    `klorb.tools.scratchpad.common.Scratchpad`), each prefixed with its 1-indexed line number
    followed by `'|'`, exactly like `ReadFile` -- but pinned to that one file, so there is no
    `filename` argument and no `readDirs` permission check to perform: the scratchpad is
    harness-managed session state, not a model-nameable path.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self._max_lines = context.process_config.read_file_max_lines

    def name(self) -> str:
        return "ReadScratchpad"

    def description(self) -> str:
        return (
            "Reads a range of lines from your scratchpad: a plain-text scratch file for "
            "recording plans, running notes, and anything else you want to track outside "
            "your own context, or share with other agents working alongside you in the same "
            "team, if configured that way. Returns up to "
            f"{self._max_lines} of its lines, each prefixed with its 1-indexed line number "
            "followed by '|' (e.g. '3|some text'), same as ReadFile -- that 'N|' prefix is "
            "not part of the scratchpad's actual content. Use start_line and end_line to page "
            "through a scratchpad larger than the per-call limit."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "start_line": {
                    "type": "integer",
                    "description": (
                        "1-indexed line to start reading from. 0 or omitted means start at "
                        "the beginning of the scratchpad."
                    ),
                },
                "end_line": {
                    "type": "integer",
                    "description": (
                        "1-indexed, inclusive line to stop reading at. Omitted means read "
                        f"up to {self._max_lines} lines from start_line."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        start_line = args.get("start_line")
        end_line = args.get("end_line")
        logger.debug("ReadScratchpad (start_line=%s, end_line=%s)", start_line, end_line)

        if start_line is not None and start_line < 0:
            raise ValueError(
                f"start_line must be >= 0, got {start_line}; there is no negative/"
                "relative-to-the-end addressing (e.g. -1 does not mean 'last line') — call "
                "with no start_line/end_line to see total_lines and find the last line's number")
        if end_line is not None and end_line < 1:
            raise ValueError(f"end_line must be >= 1, got {end_line}")

        effective_start = start_line if start_line else 1
        if end_line is not None and end_line < effective_start:
            raise ValueError(
                f"end_line ({end_line}) must be >= start_line ({effective_start})")

        path = scratchpad_path(self.context)
        with open(path, encoding="utf-8") as file:
            all_lines = file.read().splitlines()
        total_lines = len(all_lines)

        requested_end = end_line if end_line is not None else effective_start + self._max_lines - 1
        capped_end = min(requested_end, effective_start + self._max_lines - 1, total_lines)

        if capped_end >= effective_start:
            selected_lines = all_lines[effective_start - 1:capped_end]
        else:
            selected_lines = []
        content = "\n".join(f"{effective_start + i}|{line}" for i, line in enumerate(selected_lines))
        returned_end = effective_start + len(selected_lines) - \
            1 if selected_lines else effective_start - 1
        logger.debug(
            "ReadScratchpad returned lines %d-%d of %d (truncated=%s)",
            effective_start, returned_end, total_lines, returned_end < total_lines,
        )

        return {
            "start_line": effective_start,
            "end_line": returned_end,
            "total_lines": total_lines,
            "truncated": returned_end < total_lines,
            "content": content,
        }

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        if error is not None:
            return f"Read scratchpad failed: {error}"
        if not isinstance(result, dict):
            return "Read scratchpad"
        return (
            f"Read scratchpad (lines {result.get('start_line')}-{result.get('end_line')} "
            f"of {result.get('total_lines')})"
        )

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Same as the default pretty-JSON rendering, but with `result["content"]` capped to 8
        lines — a full-length `ReadScratchpad` result can be up to `self._max_lines` (200 by
        default) lines, far more than is useful to show inline.
        """
        if error is not None or not isinstance(result, dict) or "content" not in result:
            return super().detail_view(args, result, error)
        capped_result = dict(result)
        capped_result["content"] = truncate_lines(result["content"], 8)
        return super().detail_view(args, capped_result, error)
