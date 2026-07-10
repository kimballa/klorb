# © Copyright 2026 Aaron Kimball
"""The line-range read mechanic shared by `ReadFileTool` and `ReadScratchpadTool` — see
`klorb.tools.util`'s package docstring for how the two tools each hold one of these as a member
and delegate to it."""

from pathlib import Path
from typing import Any


class ReadFileCore:
    """Reads up to `max_lines` lines from `path`, prefixed with 1-indexed line numbers — the
    shared mechanic behind `ReadFileTool` and `ReadScratchpadTool`."""

    def __init__(self, max_lines: int) -> None:
        self._max_lines = max_lines

    @property
    def max_lines(self) -> int:
        """Return the per-call line cap this core was constructed with."""
        return self._max_lines

    def parameter_properties(self) -> dict[str, Any]:
        """Return the `start_line`/`end_line` JSON-schema properties shared by `ReadFileTool`
        and `ReadScratchpadTool`'s `parameters()` — each adds its own `filename` property (or
        not) and `required` list around this."""
        return {
            "start_line": {
                "type": "integer",
                "description": (
                    "1-indexed line to start reading from, counted from the beginning — never "
                    "negative or relative to the end (there is no -1). 0 or omitted means start "
                    "at the beginning."
                ),
            },
            "end_line": {
                "type": "integer",
                "description": (
                    "1-indexed, inclusive line to stop reading at. Omitted means read up to "
                    f"{self._max_lines} lines from start_line."
                ),
            },
        }

    def apply(self, path: Path, args: dict[str, Any]) -> dict[str, Any]:
        """Read `path` per `args`' `start_line`/`end_line`, returning `start_line`, `end_line`,
        `total_lines`, `truncated`, and `content` (the caller adds `filename` if it has one).
        """
        start_line = args.get("start_line")
        end_line = args.get("end_line")

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

        return {
            "start_line": effective_start,
            "end_line": returned_end,
            "total_lines": total_lines,
            "truncated": returned_end < total_lines,
            "content": content,
        }
