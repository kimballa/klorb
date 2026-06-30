# © Copyright 2026 Aaron Kimball
"""A Tool that reads a range of lines from a text file for a model."""

from typing import Any

from klorb.tools.tool import Tool

MAX_LINES = 200


class ReadFileTool(Tool):
    """Reads up to MAX_LINES lines from a text file, prefixed with 1-indexed line numbers."""

    def name(self) -> str:
        return "ReadFile"

    def description(self) -> str:
        return (
            f"Reads a text file and returns up to {MAX_LINES} of its lines, each prefixed "
            "with its 1-indexed line number. Use start_line and end_line to page through "
            "files larger than the per-call limit."
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
                        "1-indexed line to start reading from. 0 or omitted means start "
                        "at the beginning of the file."
                    ),
                },
                "end_line": {
                    "type": "integer",
                    "description": (
                        "1-indexed, inclusive line to stop reading at. Omitted means read "
                        f"up to {MAX_LINES} lines from start_line."
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

        if start_line is not None and start_line < 0:
            raise ValueError(f"start_line must be >= 0, got {start_line}")
        if end_line is not None and end_line < 1:
            raise ValueError(f"end_line must be >= 1, got {end_line}")

        # A start_line of 0 (or omitted) means "start at the beginning."
        effective_start = start_line if start_line else 1
        if end_line is not None and end_line < effective_start:
            raise ValueError(
                f"end_line ({end_line}) must be >= start_line ({effective_start})")

        with open(filename, encoding="utf-8") as file:
            all_lines = file.read().splitlines()
        total_lines = len(all_lines)

        requested_end = end_line if end_line is not None else effective_start + MAX_LINES - 1
        capped_end = min(requested_end, effective_start + MAX_LINES - 1, total_lines)

        if capped_end >= effective_start:
            selected_lines = all_lines[effective_start - 1:capped_end]
        else:
            selected_lines = []
        content = "\n".join(f"{effective_start + i}|{line}" for i, line in enumerate(selected_lines))
        returned_end = effective_start + len(selected_lines) - 1 if selected_lines else effective_start - 1

        return {
            "filename": filename,
            "start_line": effective_start,
            "end_line": returned_end,
            "total_lines": total_lines,
            "truncated": returned_end < total_lines,
            "content": content,
        }
