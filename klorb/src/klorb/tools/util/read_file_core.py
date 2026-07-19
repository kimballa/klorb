# © Copyright 2026 Aaron Kimball
"""The line-range read mechanic shared by `ReadFileTool` and `ReadScratchpadTool` — see
`klorb.tools.util`'s package docstring for how the two tools each hold one of these as a member
and delegate to it."""

from importlib.resources.abc import Traversable
from pathlib import Path
from typing import IO, Any


class ReadFileCore:
    """Reads up to `max_lines` lines from `path`, prefixed with 1-indexed line numbers — the
    shared mechanic behind `ReadFileTool`, `ReadScratchpadTool`, `ReadMemoryTool`, and
    `ReadSkillFileTool`.

    Reads go through `open_resource()`, which handles both a real filesystem `Path` and any other
    `importlib.resources` `Traversable` (a packaged resource that may have no filesystem path at
    all — e.g. `ReadSkillFileTool` reading an `internal`-tier skill file from a zip/wheel-installed
    klorb, see docs/specs/skills.md). A subclass may override `open_resource()` to obtain the
    handle differently."""

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

    def open_resource(self, path: Traversable) -> IO[str]:
        """Open `path` for text reading and return the file handle `apply()` reads from. A real
        filesystem `Path` is opened with the builtin `open()`; any other `Traversable` (a packaged
        resource with no filesystem path) is opened via its own `open()` through the
        `importlib.resources` loader. A subclass may override to obtain the handle differently."""
        if isinstance(path, Path):
            return open(path, encoding="utf-8")
        return path.open("r", encoding="utf-8")

    def apply(self, path: Traversable, args: dict[str, Any]) -> dict[str, Any]:
        """Read `path` per `args`' `start_line`/`end_line`, returning `start_line`, `end_line`,
        `total_lines`, `truncated`, `content` (the caller adds `filename`/`name` if it has one),
        and, when `truncated` is true, `next_start_line` -- the `start_line` to pass on the next
        call to continue reading where this one left off, so a caller paging through a large
        file doesn't have to compute `end_line + 1` itself. `args`' `start_line`/`end_line` are
        validated (raising `ValueError`) before `path` is opened via `open_resource()`.
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

        with self.open_resource(path) as file:
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

        truncated = returned_end < total_lines
        result: dict[str, Any] = {
            "start_line": effective_start,
            "end_line": returned_end,
            "total_lines": total_lines,
            "truncated": truncated,
            "content": content,
        }
        if truncated:
            result["next_start_line"] = returned_end + 1
        return result
