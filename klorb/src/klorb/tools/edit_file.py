# © Copyright 2026 Aaron Kimball
"""A Tool that replaces a verified, inclusive line range in a text file for a model."""

import logging
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import evaluate_write, resolve_within_workspace
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool, truncate_lines
from klorb.tools.util import EditFileCore

logger = logging.getLogger(__name__)


class EditFileTool(Tool):
    """Replaces the inclusive line range `[start_line, end_line]` of a text file with
    `new_text` — delegating that mechanic to `self.edit_file_core` (a
    `klorb.tools.util.EditFileCore`), the same one `EditScratchpadTool` uses, so it's written
    and tested once. See your system prompt's guidance on `EditFile`/`EditScratchpad` for the
    `start_text`/`end_text`/`context_before`/`context_after` conventions, drift tolerance, and
    "Ambiguous match" handling.

    `filename` is confined to `SessionConfig.workspace.path` and further checked against
    `writeDirs` (see `klorb.permissions.workspace.evaluate_write`) before any disk I/O.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self.edit_file_core = EditFileCore(context.process_config.edit_file_drift_search_radius)

    def name(self) -> str:
        return "EditFile"

    def description(self) -> str:
        return (
            "Replaces the inclusive 1-indexed line range [start_line, end_line] of a text "
            "file with new_text. See your system prompt's guidance on EditFile/EditScratchpad "
            "for how start_text/end_text/context_before/context_after work, how drift "
            "tolerance and 'Ambiguous match' errors work, and the empty-file/insert/delete "
            "conventions."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Path to the text file to edit.",
                },
                **self.edit_file_core.parameter_properties(),
            },
            "required": ["filename", "start_line", "end_line", "start_text", "end_text", "new_text"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        filename = args["filename"]
        logger.debug("EditFile %s (start_line=%s, end_line=%s)",
                     filename, args.get("start_line"), args.get("end_line"))

        path = resolve_within_workspace(self.context, filename)
        raise_if_not_allowed(
            evaluate_write(self.context, path), resource_description=f"write to {path}",
            path=path, is_write=True)

        result = self.edit_file_core.apply(
            path, args, subject=filename, reread_hint=f"re-ReadFile {filename}")
        result["filename"] = filename

        logger.debug(
            "EditFile %s replaced lines %d-%d (now %d-%d) of what is now a %d-line file",
            filename, result["requested_start_line"], result["requested_end_line"],
            result["start_line"], result["end_line"], result["new_total_lines"],
        )
        return result

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
