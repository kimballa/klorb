# © Copyright 2026 Aaron Kimball
"""A Tool that reads a range of lines from a text file for a model."""

import logging
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import resolve_and_evaluate_read
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool
from klorb.tools.tool import truncate_lines

logger = logging.getLogger(__name__)


class ReadFileTool(Tool):
    """Reads up to `max_lines` lines from a text file, prefixed with 1-indexed line numbers.

    `filename` is resolved and checked against `readDirs` (see
    `klorb.permissions.workspace.resolve_and_evaluate_read`) before the file is opened —
    confined to `SessionConfig.workspace_root` unless `ProcessConfig.workspace.trusted`.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self._max_lines = context.process_config.read_file_max_lines

    def name(self) -> str:
        return "ReadFile"

    def description(self) -> str:
        return (
            f"Reads a text file and returns up to {self._max_lines} of its lines, each "
            "prefixed with its 1-indexed line number followed by '|' (e.g. '3|some text'), "
            "purely so you can tell which line goes where — that 'N|' prefix is not part of the "
            "file's actual content. When quoting a line's content back to another tool (e.g. "
            "EditFile), use the text after the '|' only, never "
            "the line number or the '|' itself. Use start_line and end_line to page through "
            "files larger than the per-call limit. There is no way to address a line relative "
            "to the end of the file (e.g. -1 does not mean 'last line') — start_line and "
            "end_line only accept 1-indexed positions counted from the beginning. To find or "
            "read the last line of a file whose length you don't already know, call with no "
            "start_line/end_line (or start_line=1): the result's total_lines tells you the "
            "last line's number, which you can then target directly if needed."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Path to the text file to read.",
                },
                "start_line": {
                    "type": "integer",
                    "description": (
                        "1-indexed line to start reading from, counted from the beginning of "
                        "the file — never negative or relative to the end (there is no -1)."
                        "0 or omitted means start at the beginning of the file."
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
            "required": ["filename"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        filename = args["filename"]
        start_line = args.get("start_line")
        end_line = args.get("end_line")
        logger.debug("ReadFile %s (start_line=%s, end_line=%s)", filename, start_line, end_line)

        if start_line is not None and start_line < 0:
            raise ValueError(
                f"start_line must be >= 0, got {start_line}; there is no negative/"
                "relative-to-the-end addressing (e.g. -1 does not mean 'last line') — call "
                "with no start_line/end_line to see total_lines and find the last line's number")
        if end_line is not None and end_line < 1:
            raise ValueError(f"end_line must be >= 1, got {end_line}")

        # A start_line of 0 (or omitted) means "start at the beginning."
        effective_start = start_line if start_line else 1
        if end_line is not None and end_line < effective_start:
            raise ValueError(
                f"end_line ({end_line}) must be >= start_line ({effective_start})")

        path, verdict = resolve_and_evaluate_read(self.context, filename)
        raise_if_not_allowed(verdict, resource_description=f"read {path}", path=path, is_write=False)

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
            "ReadFile %s returned lines %d-%d of %d (truncated=%s)",
            filename, effective_start, returned_end, total_lines, returned_end < total_lines,
        )

        return {
            "filename": filename,
            "start_line": effective_start,
            "end_line": returned_end,
            "total_lines": total_lines,
            "truncated": returned_end < total_lines,
            "content": content,
        }

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        filename = args.get("filename", "?")
        if error is not None:
            return f"Read file: {filename} failed: {error}"
        if not isinstance(result, dict):
            return f"Read file: {filename}"
        return (
            f"Read file: {result.get('filename', filename)} "
            f"(lines {result.get('start_line')}-{result.get('end_line')} of {result.get('total_lines')})"
        )

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Same as the default pretty-JSON rendering, but with `result["content"]` capped to 8
        lines — a full-length `ReadFile` result can be up to `self._max_lines` (200 by default)
        lines, far more than is useful to show inline.
        """
        if error is not None or not isinstance(result, dict) or "content" not in result:
            return super().detail_view(args, result, error)
        capped_result = dict(result)
        capped_result["content"] = truncate_lines(result["content"], 8)
        return super().detail_view(args, capped_result, error)
