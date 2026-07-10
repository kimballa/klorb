# © Copyright 2026 Aaron Kimball
"""A Tool that reads a range of lines from the active session's scratchpad file for a model."""

import logging
from typing import Any

from klorb.tools.scratchpad.common import scratchpad_path
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool, truncate_lines
from klorb.tools.util import ReadFileCore

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
