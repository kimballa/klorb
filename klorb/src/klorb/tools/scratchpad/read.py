# © Copyright 2026 Aaron Kimball
"""A Tool that reads a range of lines from the active session's scratchpad file for a model."""

import logging
from typing import Any

from klorb.tools.scratchpad.common import scratchpad_path
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import ReadPreview, Tool, truncate_lines
from klorb.tools.util import (
    READ_PREVIEW_MAX_LINES,
    FullFileView,
    ReadFileCore,
    parse_numbered_content,
    read_full_file_lines,
)

logger = logging.getLogger(__name__)


class ReadScratchpadTool(Tool):
    """Reads up to `max_lines` lines from the active session's scratchpad file (see
    `klorb.tools.scratchpad.common.Scratchpad`), each prefixed with its 1-indexed line number —
    delegating that mechanic to `self.read_file_core` (the same `klorb.tools.util.ReadFileCore`
    `ReadFileTool` uses) -- but pinned to that one file, so there is no `filename` argument and
    no `readDirs` permission check to perform: the scratchpad is harness-managed session state,
    not a model-nameable path.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self.read_file_core = ReadFileCore(context.process_config.read_file_max_lines)

    def name(self) -> str:
        return "ReadScratchpad"

    def category(self) -> str:
        return "SCRATCHPAD"

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Reads a range of lines from your scratchpad: a plain-text scratch file for "
            "recording plans, running notes, and anything else you want to track outside "
            "your own context, or share with other agents working alongside you in the same "
            f"team, if configured that way. Returns up to {self.read_file_core.max_lines} of "
            "its lines, each prefixed with its 1-indexed line number followed by '|', same "
            "as ReadFile."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": self.read_file_core.parameter_properties(),
            "required": [],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        logger.debug("ReadScratchpad (start_line=%s, end_line=%s)",
                     args.get("start_line"), args.get("end_line"))
        path = scratchpad_path(self.context)
        result = self.read_file_core.apply(path, args)
        logger.debug(
            "ReadScratchpad returned lines %d-%d of %d (truncated=%s)",
            result["start_line"], result["end_line"], result["total_lines"], result["truncated"],
        )
        return result

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
        lines — a full-length `ReadScratchpad` result can be up to `self.read_file_core.max_lines`
        (200 by default) lines, far more than is useful to show inline.
        """
        if error is not None or not isinstance(result, dict) or "content" not in result:
            return super().detail_view(args, result, error)
        capped_result = dict(result)
        capped_result["content"] = truncate_lines(result["content"], 8)
        return super().detail_view(args, capped_result, error)

    def read_preview(
        self, args: dict[str, Any], result: Any = None, error: str | None = None,
    ) -> ReadPreview | None:
        if error is not None or not isinstance(result, dict) or "content" not in result:
            return None
        lines = parse_numbered_content(result["content"])
        return ReadPreview(
            label=self.summary(args, result, error), preview_lines=lines[:READ_PREVIEW_MAX_LINES],
            truncated=len(lines) > READ_PREVIEW_MAX_LINES or bool(result.get("truncated")),
            open_full=lambda: self._open_full_view(result.get("start_line", 1)))

    def _open_full_view(self, scroll_to_line: int) -> FullFileView:
        """Passively re-read the scratchpad in full for the click-to-expand overlay -- no
        permission check to redo, since the scratchpad has none in the first place."""
        path = scratchpad_path(self.context)
        return read_full_file_lines(lambda: self.read_file_core.open_resource(path), scroll_to_line)
