# © Copyright 2026 Aaron Kimball
"""A Tool that replaces a verified, inclusive line range in a text file for a model."""

import logging
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import evaluate_write, resolve_within_workspace
from klorb.tools.line_range_edit import resolve_line_range_edit
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool, truncate_lines

logger = logging.getLogger(__name__)


class EditFileTool(Tool):
    """Replaces the inclusive line range `[start_line, end_line]` of a text file with
    `new_text`, after locating `start_text`/`end_text` at (or near) those lines.
    `start_line`/`end_line` are treated as a location hint rather than a hard requirement: if
    the file has drifted since the caller last saw it (e.g. from an earlier edit in the same
    turn shifting later lines), `apply()` searches within `context.process_config
    .edit_file_drift_search_radius` lines of the hint for a unique location where
    `start_text`/`end_text` still match at the same relative span, and edits there instead of
    failing — see `klorb.tools.line_range_edit.resolve_line_range_edit`, the shared mechanic
    this also powers for `EditScratchpadTool`. No match within that radius, or more than one,
    still raises `ValueError`.

    There is no separate insert or delete tool: insert without deleting by setting
    `start_line == end_line` and folding that line's original text into `new_text`; delete by
    passing an empty `new_text`. The one exception is an empty file (`total_lines == 0`),
    where there's no anchor line to replace — the only valid call there is
    `start_line=1, end_line=0, start_text="", end_text=""`.

    `filename` is confined to `SessionConfig.workspace.path` and further checked against
    `writeDirs` (see `klorb.permissions.workspace.evaluate_write`) before any disk I/O.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self._drift_search_radius = context.process_config.edit_file_drift_search_radius

    def name(self) -> str:
        return "EditFile"

    def description(self) -> str:
        return (
            "Replaces the inclusive 1-indexed line range [start_line, end_line] of a text "
            "file with new_text. Most common mistake: pasting the whole multi-line block "
            "being replaced into start_text/end_text. Don't — start_text and end_text are "
            "each exactly ONE line of the file's CURRENT content: the literal first and "
            "last lines of the range named by start_line/end_line, verbatim and without a "
            "trailing newline ('\\n'), used only to verify you're editing the right spot. "
            "Every other old line, and all of the new content (any number of lines, or "
            "none), goes in new_text instead — new_text is the only field that may be "
            "multi-line. Example: to replace lines 10-12 (`def foo():` / `    x = 1` / "
            "`    return x`) with a two-line body, call start_line=10, "
            "start_text=\"def foo():\", end_line=12, end_text=\"    return x\", "
            "new_text=\"def foo():\\n    return x * 2\". start_text, end_text, new_text, "
            "context_before, and context_after are all raw file content: never include "
            "the 'N|' line-number prefix that ReadFile prepends to each line for display "
            "purposes, and type special characters (em dashes, curly quotes, etc.) "
            "literally rather than as an escape sequence like \"\\u2014\". "
            "start_line/end_line are only a location hint, not exact coordinates: some "
            "offset drift (e.g. from an earlier edit) is tolerated — if start_text/end_text "
            "don't match exactly at the hint but match together, at the same relative "
            "offset, within a few lines of it, the edit is applied there. The response "
            "will report the corrected start_line/end_line plus line_hint_matched=false. "
            "re-ReadFile is only needed when no matching location is found nearby. When "
            "more than one match is found ('Ambiguous match' error), retry with "
            "context_before / context_after arguments (lines of raw content immediately "
            "before start_line / after end_line, unique to the intended location) to "
            "disambiguate, exactly as reported in the error. To insert new content "
            "into a non-empty file without deleting anything set start_line == "
            "end_line to an existing line number and fold that line's original text "
            "into new_text alongside the new content. Setting end_line to start_line "
            "minus 1 (a zero-width range) is invalid for any non-empty file. To "
            "insert into a completely empty file (0 lines), the only valid call is "
            "start_line=1, end_line=0, start_text=\"\", end_text=\"\". To delete "
            "lines, pass empty new_text. Applying multiple edits to the same file "
            "bottom-to-top (greatest line numbers first) avoids drift."
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
                        "The file's CURRENT content at start_line — exactly one line, verbatim, "
                        "no trailing newline. Never the multi-line block being replaced (that's "
                        "new_text) and never the replacement's first line either; this is only "
                        "for verifying you're editing the right spot."
                    ),
                },
                "end_text": {
                    "type": "string",
                    "description": (
                        "The file's CURRENT content at end_line — exactly one line, verbatim, "
                        "no trailing newline. Same rule as start_text: never the multi-line "
                        "block being replaced, and never the replacement's last line either."
                    ),
                },
                "new_text": {
                    "type": "string",
                    "description": (
                        "Full replacement content for [start_line, end_line], as raw text with "
                        "'\\n' between lines. Unlike start_text/end_text, this may span any "
                        "number of lines, including zero (an empty string deletes the range)."
                    ),
                },
                "context_before": {
                    "type": ["string", "null"],
                    "description": (
                        "Optional. Raw content (no line numbers) immediately before start_line, "
                        "used only to disambiguate multiple start_text/end_text matches. "
                        "Only include after an 'Ambiguous match' error. The empty string \"\" "
                        "means that there must be absolutely nothing before start_line "
                        "(i.e. it's the file's first line). To omit, just send null."
                    ),
                },
                "context_after": {
                    "type": ["string", "null"],
                    "description": (
                        "Optional. Like context_before, but matches immediately after end_line "
                        "-- and \"\" asserts end_line is the file's last line."
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
        context_before = args.get("context_before")
        context_after = args.get("context_after")
        logger.debug("EditFile %s (start_line=%s, end_line=%s)", filename, start_line, end_line)

        for label, value in (("start_line", start_line), ("end_line", end_line)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{label} must be an integer, got {value!r} ({type(value).__name__})")

        for label, value in (("start_text", start_text), ("end_text", end_text)):
            if "\n" in value:
                first_line = value.split("\n", 1)[0]
                raise ValueError(
                    f"{label} must be exactly one line, with no '\\n' character: got "
                    f"{value!r}, which spans multiple lines. Send only the single line of "
                    f"{'start_line' if label == 'start_text' else 'end_line'}'s raw content, "
                    f"not the whole range being replaced. Did you mean {first_line!r}?")

        path = resolve_within_workspace(self.context, filename)
        raise_if_not_allowed(
            evaluate_write(self.context, path), resource_description=f"write to {path}",
            path=path, is_write=True)

        raw = path.read_text(encoding="utf-8")
        original_ends_with_newline = raw.endswith("\n")
        all_lines = raw.splitlines()
        total_lines = len(all_lines)

        edit = resolve_line_range_edit(
            all_lines, start_line=start_line, end_line=end_line, start_text=start_text,
            end_text=end_text, new_text=new_text, context_before=context_before,
            context_after=context_after, drift_search_radius=self._drift_search_radius,
            subject=filename, reread_hint=f"re-ReadFile {filename}")
        resolved_start_line = edit.resolved_start_line
        resolved_end_line = edit.resolved_end_line
        line_hint_matched = edit.line_hint_matched
        new_lines = edit.new_lines

        if resolved_end_line == total_lines:
            terminate_with_newline = bool(new_lines)
        else:
            terminate_with_newline = original_ends_with_newline

        content = "\n".join(new_lines)
        if terminate_with_newline:
            content += "\n"
        path.write_text(content, encoding="utf-8")

        new_total_lines = len(new_lines)
        inserted_line_count = len(new_text.splitlines())
        snippet_end = resolved_start_line + inserted_line_count - 1
        snippet = "\n".join(
            f"{resolved_start_line + i}|{line}" for i, line in enumerate(new_text.splitlines()))

        logger.debug(
            "EditFile %s replaced lines %d-%d (now %d-%d) of what is now a %d-line file",
            filename, resolved_start_line, resolved_end_line, resolved_start_line, snippet_end,
            new_total_lines,
        )

        return {
            "filename": filename,
            "requested_start_line": start_line,
            "requested_end_line": end_line,
            "start_line": resolved_start_line,
            "end_line": snippet_end,
            "line_hint_matched": line_hint_matched,
            "new_total_lines": new_total_lines,
            "content": snippet,
        }

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """`"Edit file: foo.py (+A/-R)"`, where the added/removed line counts are computed from
        the call's own `start_line`/`end_line`/`new_text` args (not `result`), so the summary
        is identical on success and failure and unaffected by drift relocation, which preserves
        the requested span's length.
        """
        filename = args.get("filename", "?")
        diff = ""
        start_line, end_line, new_text = args.get("start_line"), args.get("end_line"), args.get("new_text")
        if isinstance(start_line, int) and isinstance(end_line, int) and isinstance(new_text, str):
            removed = end_line - start_line + 1
            added = new_text.count("\n") + 1 if new_text else 0
            diff = f" (+{added}/-{removed})"
        base = f"Edit file: {filename}{diff}"
        return base if error is None else f"{base} failed: {error}"

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Same as the default pretty-JSON rendering, but with `result["content"]` (the edited
        region) capped to 8 lines — a full-file rewrite via one large `new_text` could otherwise
        dump hundreds of lines here.
        """
        if error is not None or not isinstance(result, dict) or "content" not in result:
            return super().detail_view(args, result, error)
        capped_result = dict(result)
        capped_result["content"] = truncate_lines(result["content"], 8)
        return super().detail_view(args, capped_result, error)
