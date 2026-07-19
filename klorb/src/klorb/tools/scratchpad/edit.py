# © Copyright 2026 Aaron Kimball
"""A Tool that replaces a verified, inclusive line range in the active session's scratchpad
file for a model."""

import logging
from typing import Any

from klorb.tools.scratchpad.common import scratchpad_path
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool, truncate_lines
from klorb.tools.util import EditFileCore

logger = logging.getLogger(__name__)


class EditScratchpadTool(Tool):
    """Replaces the inclusive line range `[start_line, end_line]` of the active session's
    scratchpad file with `new_text` — delegating that mechanic to `self.edit_file_core` (the
    same `klorb.tools.util.EditFileCore` `EditFileTool` uses) -- but pinned to the scratchpad
    file, so there is no `filename` argument and no `writeDirs` permission check to perform: the
    scratchpad is harness-managed session state, not a model-nameable path. See your system
    prompt's guidance on `EditFile`/`EditScratchpad` for the `start_text`/`end_text`/
    `context_before`/`context_after` conventions, drift tolerance, and "Ambiguous match"
    handling.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self.edit_file_core = EditFileCore(context.process_config.edit_file_drift_search_radius)

    def name(self) -> str:
        return "EditScratchpad"

    def description(self) -> str:
        return (
            "Replaces the inclusive 1-indexed line range [start_line, end_line] of your "
            "scratchpad with new_text -- same mechanics as EditFile, but for the scratchpad "
            "rather than a named file, so there is no filename argument. See the system "
            "prompt's guidance on EditFile and EditScratchpad."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": self.edit_file_core.parameter_properties(),
            "required": ["new_text"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        logger.debug("EditScratchpad (start_line=%s, end_line=%s)",
                     args.get("start_line"), args.get("end_line"))
        path = scratchpad_path(self.context)
        result = self.edit_file_core.apply(
            path, args, subject="the scratchpad", reread_hint="re-ReadScratchpad your scratchpad")
        logger.debug(
            "EditScratchpad replaced lines %d-%d (now %d-%d) of what is now a %d-line "
            "scratchpad", result["requested_start_line"], result["requested_end_line"],
            result["start_line"], result["end_line"], result["new_total_lines"],
        )
        return result

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """`"Edit scratchpad (+A/-R)"`, where the added/removed line counts prefer `result`'s
        `requested_start_line`/`requested_end_line` (the call's line hint after normalization --
        alias-resolved, and, for an `old_text` call that omitted `end_line`, inferred from its
        line count) when a `result` is available, falling back to the call's own raw args
        otherwise (a failed call has no `result`). Unaffected by drift relocation either way,
        which preserves the requested span's length.
        """
        diff = ""
        if isinstance(result, dict):
            start_line, end_line = result.get("requested_start_line"), result.get("requested_end_line")
        else:
            start_line, end_line = args.get("start_line"), args.get("end_line")
        new_text = args.get("new_text")
        if isinstance(start_line, int) and isinstance(end_line, int) and isinstance(new_text, str):
            removed = end_line - start_line + 1
            added = new_text.count("\n") + 1 if new_text else 0
            diff = f" (+{added}/-{removed})"
        base = f"Edit scratchpad{diff}"
        return base if error is None else f"{base} failed: {error}"

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Same as the default pretty-JSON rendering, but with `result["content"]` (the edited
        region) capped to 8 lines — a full-scratchpad rewrite via one large `new_text` could
        otherwise dump hundreds of lines here.
        """
        if error is not None or not isinstance(result, dict) or "content" not in result:
            return super().detail_view(args, result, error)
        capped_result = dict(result)
        capped_result["content"] = truncate_lines(result["content"], 8)
        return super().detail_view(args, capped_result, error)
