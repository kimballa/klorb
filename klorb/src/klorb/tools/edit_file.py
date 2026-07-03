# © Copyright 2026 Aaron Kimball
"""A Tool that replaces a verified, inclusive line range in a text file for a model."""

import logging
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import evaluate_write, resolve_within_workspace
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)


class EditFileTool(Tool):
    """Replaces the inclusive line range `[start_line, end_line]` of a text file with
    `new_text`, after verifying `start_text`/`end_text` still match what's actually at those
    lines (catching stale line numbers from an earlier edit or `ReadFile` call).

    There is no separate insert or delete tool: insert without deleting by setting
    `start_line == end_line` and folding that line's original text into `new_text`; delete by
    passing an empty `new_text`. The one exception is an empty file (`total_lines == 0`),
    where there's no anchor line to replace — the only valid call there is
    `start_line=1, end_line=0, start_text="", end_text=""`.

    `filename` is confined to `SessionConfig.workspace_root` and further checked against
    `writeDirs` (see `klorb.permissions.workspace.evaluate_write`) before any disk I/O.
    """

    def name(self) -> str:
        return "EditFile"

    def description(self) -> str:
        return (
            "Replaces the inclusive line range start_line..end_line of a text file with "
            "new_text. start_text, end_text, and new_text are all raw file content: never "
            "include the 'N|' line-number prefix that ReadFile prepends to each line for "
            "display purposes — that prefix is not part of the file, only a label for which "
            "line goes where. start_text and end_text must exactly match the current content of "
            "start_line and end_line (with no line-number prefix) or the edit is rejected — "
            "re-ReadFile if this happens, since it means the line numbers have "
            "drifted (e.g. from an earlier edit in this turn). "
            "There is no separate insert or delete tool. To insert new content into a "
            "non-empty file without deleting anything — including inserting a new first "
            "line — set start_line == end_line to an existing line number and fold that "
            "line's original text into new_text alongside the new content (e.g. to insert "
            "a line before the current line 1, call with start_line=1, end_line=1, "
            "start_text and end_text both equal to the current line 1's content, and "
            "new_text = the new line followed by that original line 1 content). Setting "
            "end_line to start_line minus 1 (a zero-width range) is invalid for any "
            "non-empty file. The only case where end_line = start_line - 1 is allowed is "
            "a completely empty file (0 lines), where the only valid call is "
            "start_line=1, end_line=0, start_text=\"\", end_text=\"\". "
            "To delete a range, pass empty new_text. "
            "To make multiple edits to the same file, apply them "
            "bottom-to-top (highest line numbers first), since every edit shifts the line "
            "numbers of everything below it — otherwise re-ReadFile between edits."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Path to the text file to edit.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "1-indexed line to start replacing from.",
                },
                "end_line": {
                    "type": "integer",
                    "description": "1-indexed, inclusive line to stop replacing at.",
                },
                "start_text": {
                    "type": "string",
                    "description": (
                        "Expected current content of start_line, for drift verification. Raw "
                        "line content only — do not include line-number prefix."
                    ),
                },
                "end_text": {
                    "type": "string",
                    "description": (
                        "Expected current content of end_line, for drift verification. Raw "
                        "line content only — do not include line-number prefix."
                    ),
                },
                "new_text": {
                    "type": "string",
                    "description": (
                        "Replacement content for the range. Empty string deletes the range. "
                        "Raw line content only."
                    ),
                },
            },
            "required": ["filename", "start_line", "end_line", "start_text", "end_text", "new_text"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        filename = args["filename"]
        start_line = args["start_line"]
        end_line = args["end_line"]
        start_text = args["start_text"]
        end_text = args["end_text"]
        new_text = args["new_text"]
        logger.debug("EditFile %s (start_line=%s, end_line=%s)", filename, start_line, end_line)

        for label, value in (("start_line", start_line), ("end_line", end_line)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{label} must be an integer, got {value!r} ({type(value).__name__})")

        path = resolve_within_workspace(self.context, filename)
        raise_if_not_allowed(evaluate_write(self.context, path), resource_description=f"write to {path}")

        raw = path.read_text(encoding="utf-8")
        original_ends_with_newline = raw.endswith("\n")
        all_lines = raw.splitlines()
        total_lines = len(all_lines)

        if total_lines == 0:
            if not (start_line == 1 and end_line == 0):
                raise ValueError(
                    f"{filename} is empty; the only valid edit is "
                    "start_line=1, end_line=0, start_text=\"\", end_text=\"\" to insert content")
            if start_text != "" or end_text != "":
                raise ValueError(
                    f"{filename} is empty; start_text and end_text must both be \"\", "
                    f"got start_text={start_text!r}, end_text={end_text!r}")
        else:
            if start_line < 1 or end_line < start_line or end_line > total_lines:
                raise ValueError(
                    f"start_line={start_line}, end_line={end_line} out of range for a "
                    f"{total_lines}-line file (expected 1 <= start_line <= end_line <= {total_lines})")
            if all_lines[start_line - 1] != start_text:
                raise ValueError(
                    f"start_text does not match line {start_line}'s actual content: "
                    f"{all_lines[start_line - 1]!r} != {start_text!r}; re-ReadFile {filename}")
            if all_lines[end_line - 1] != end_text:
                raise ValueError(
                    f"end_text does not match line {end_line}'s actual content: "
                    f"{all_lines[end_line - 1]!r} != {end_text!r}; re-ReadFile {filename}")

        new_lines = all_lines[:start_line - 1] + new_text.splitlines() + all_lines[end_line:]

        if end_line == total_lines:
            terminate_with_newline = bool(new_lines)
        else:
            terminate_with_newline = original_ends_with_newline

        content = "\n".join(new_lines)
        if terminate_with_newline:
            content += "\n"
        path.write_text(content, encoding="utf-8")

        new_total_lines = len(new_lines)
        inserted_line_count = len(new_text.splitlines())
        snippet_end = start_line + inserted_line_count - 1
        snippet = "\n".join(
            f"{start_line + i}|{line}" for i, line in enumerate(new_text.splitlines()))

        logger.debug(
            "EditFile %s replaced lines %d-%d (now %d-%d) of what is now a %d-line file",
            filename, start_line, end_line, start_line, snippet_end, new_total_lines,
        )

        return {
            "filename": filename,
            "start_line": start_line,
            "end_line": snippet_end,
            "new_total_lines": new_total_lines,
            "content": snippet,
        }
