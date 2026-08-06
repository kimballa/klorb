# © Copyright 2026 Aaron Kimball
"""A Tool that replaces a verified, inclusive line range in the active session's scratchpad
file for a model."""

import logging
from typing import Any

from klorb.tools.scratchpad.common import scratchpad_path
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import DiffPreview, Tool, truncate_lines
from klorb.tools.util import DiffHunk, EditFileCore

logger = logging.getLogger(__name__)


class EditScratchpadTool(Tool):
    """Replaces a block of the active session's scratchpad file's current content with
    `new_text` — delegating that mechanic to `self.edit_file_core` (the same
    `klorb.tools.util.EditFileCore` `EditFileTool` uses) -- but pinned to the scratchpad file,
    so there is no `filename` argument and no `writeDirs` permission check to perform: the
    scratchpad is harness-managed session state, not a model-nameable path. See your system
    prompt's guidance on `EditFile`/`EditScratchpad` for the `old_text`/`old_text_start`/
    `old_text_end` conventions and "Ambiguous match" handling.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self.edit_file_core = EditFileCore()

    def name(self) -> str:
        return "EditScratchpad"

    def category(self) -> str:
        return "SCRATCHPAD"

    def is_read_only(self) -> bool:
        """`True` despite writing to disk: the scratchpad is harness-managed shared
        collaboration state, not a user file or environment `is_read_only()` is meant to
        protect.
        """
        return True

    def description(self) -> str:
        return (
            "Replaces a block of your scratchpad's current content with new_text -- same "
            "mechanics as EditFile, but for the scratchpad rather than a named file, so there "
            "is no filename argument. See the system prompt's guidance on EditFile and "
            "EditScratchpad."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": self.edit_file_core.parameter_properties(),
            "required": ["new_text"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        logger.debug("EditScratchpad")
        path = scratchpad_path(self.context)
        result = self.edit_file_core.apply(
            path, args, subject="the scratchpad", reread_hint="re-ReadScratchpad your scratchpad",
            create_hint="(unexpected -- the scratchpad is harness-managed and should always exist)")
        logger.debug(
            "EditScratchpad replaced %d line(s) at line %d of what is now a %d-line scratchpad",
            result["replaced_lines"], result["start_line"], result["new_total_lines"],
        )
        return result

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """`"Edit scratchpad (+A/-R)"`, where the added/removed line counts come from `result`'s
        `replaced_lines` and the call's `new_text` -- only available on success, since a failed
        match never resolves a location to count lines removed from."""
        diff = ""
        new_text = args.get("new_text")
        if isinstance(result, dict) and isinstance(new_text, str):
            removed = result.get("replaced_lines")
            if isinstance(removed, int):
                added = new_text.count("\n") + 1 if new_text else 0
                diff = f" (+{added}/-{removed})"
        base = f"Edit scratchpad{diff}"
        return base if error is None else f"{base} failed: {error}"

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Same as the default pretty-JSON rendering, but with `result["post_edit_content"]` (the edited
        region) capped to 8 lines — a full-scratchpad rewrite via one large `new_text` could
        otherwise dump hundreds of lines here.
        """
        if error is not None or not isinstance(result, dict) or "post_edit_content" not in result:
            return super().detail_view(args, result, error)
        capped_result = dict(result)
        capped_result["post_edit_content"] = truncate_lines(result["post_edit_content"], 8)
        return super().detail_view(args, capped_result, error)

    def diff_preview(
        self, args: dict[str, Any], result: Any = None, error: str | None = None,
    ) -> DiffPreview | None:
        if error is not None or not isinstance(result, dict) or "diff" not in result:
            return None
        hunks = [DiffHunk.model_validate(hunk) for hunk in result["diff"]]
        return DiffPreview(label=self.summary(args, result, error), hunks=hunks)
