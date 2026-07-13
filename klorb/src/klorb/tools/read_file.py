# © Copyright 2026 Aaron Kimball
"""A Tool that reads a range of lines from a text file for a model."""

import logging
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import resolve_and_evaluate_read
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool, truncate_lines
from klorb.tools.util import ReadFileCore

logger = logging.getLogger(__name__)


class ReadFileTool(Tool):
    """Reads up to `max_lines` lines from a text file, prefixed with 1-indexed line numbers —
    delegating that mechanic to `self.read_file_core` (a `klorb.tools.util.ReadFileCore`), the
    same one `ReadScratchpadTool` uses, so it's written and tested once.

    `filename` is resolved and checked against `readDirs` (see
    `klorb.permissions.workspace.resolve_and_evaluate_read`) before the file is opened —
    confined to `SessionConfig.workspace.path` unless `SessionConfig.workspace.trusted`.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self.read_file_core = ReadFileCore(context.process_config.read_file_max_lines)

    def name(self) -> str:
        return "ReadFile"

    def description(self) -> str:
        return (
            f"Reads a text file and returns up to {self.read_file_core.max_lines} of its "
            "lines, each prefixed with its 1-indexed line number followed by '|' (e.g. "
            "'3|some text'), purely so you can tell which line goes where — that 'N|' prefix "
            "is not part of the file's actual content. When quoting a line's content back to "
            "another tool (e.g. EditFile), use the text after the '|' only, never the line "
            "number or the '|' itself. Use start_line and end_line to page through files "
            "larger than the per-call limit. There is no way to address a line relative to "
            "the end of the file (e.g. -1 does not mean 'last line'). To find or read the "
            "last line of a file whose length you don't already know, call with no "
            "start_line/end_line (or start_line=1): the result's total_lines tells you the "
            "last line's number, which you can then target directly if needed."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Path to the text file to read.",
                },
                **self.read_file_core.parameter_properties(),
            },
            "required": ["filename"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        try:
            filename = args["filename"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'filename'. Provide the path of the file to read.")
        logger.debug("ReadFile %s (start_line=%s, end_line=%s)",
                     filename, args.get("start_line"), args.get("end_line"))

        path, verdict = resolve_and_evaluate_read(self.context, filename)
        raise_if_not_allowed(verdict, resource_description=f"read {path}", path=path, is_write=False)

        result = self.read_file_core.apply(path, args)
        result["filename"] = filename
        logger.debug(
            "ReadFile %s returned lines %d-%d of %d (truncated=%s)",
            filename, result["start_line"], result["end_line"], result["total_lines"],
            result["truncated"],
        )
        return result

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        filename = args.get("filename", "?")
        if error is not None:
            return f"Read file: {filename} failed: {error}"
        if not isinstance(result, dict):
            return f"Read file: {filename}"
        return (
            f"Read file: {result.get('filename', filename)} "
            f"(lines {result.get('start_line')}-{result.get('end_line')} of {result.get('total_lines')})"
        )

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Same as the default pretty-JSON rendering, but with `result["content"]` capped to 8
        lines — a full-length `ReadFile` result can be up to `self.read_file_core.max_lines`
        (200 by default) lines, far more than is useful to show inline.
        """
        if error is not None or not isinstance(result, dict) or "content" not in result:
            return super().detail_view(args, result, error)
        capped_result = dict(result)
        capped_result["content"] = truncate_lines(result["content"], 8)
        return super().detail_view(args, capped_result, error)
