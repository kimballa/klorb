# © Copyright 2026 Aaron Kimball
"""Reads a range of lines from a memory file."""

import logging
from collections.abc import Sequence
from typing import Any

from klorb.tools.memory.common import (
    NAMESPACE_SCHEMA_PROPERTY,
    Namespace,
    memory_namespace_dir,
    require_workspace_namespace_accessible,
    validate_memory_filename,
)
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import ReadPreview, Tool, truncate_lines
from klorb.tools.util import (
    READ_PREVIEW_MAX_LINES,
    FullFileView,
    ReadFileCore,
    format_read_result,
    parse_numbered_content,
    read_full_file_lines,
)

logger = logging.getLogger(__name__)


class ReadMemoryTool(Tool):
    """Reads up to `max_lines` lines from a memory file, prefixed with 1-indexed line numbers.

    A read is always allowed in both namespaces with no further permission check.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self.read_file_core = ReadFileCore(
            context.process_config.read_file_max_lines,
            context.process_config.read_file_max_line_length,
        )

    def name(self) -> str:
        return "ReadMemory"

    def aliases(self) -> Sequence[str]:
        return ("ReadMemories", "MemoryRead")

    def category(self) -> str:
        return "MEMORY"

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Reads a memory file (see ListMemories/SearchMemories) and returns up to "
            f"{self.read_file_core.max_lines} of its lines, each prefixed with its "
            "line number followed by '|', same as ReadFile. Use start_line and end_line to "
            "page through a long memory."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "namespace": NAMESPACE_SCHEMA_PROPERTY,
                "filename": {
                    "type": "string",
                    "description": "Name of the memory file to read, e.g. 'user-preferences.md'.",
                },
                **self.read_file_core.parameter_properties(),
            },
            "required": ["namespace", "filename"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        try:
            namespace = args["namespace"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'namespace'. Must be 'global' or 'workspace'.")
        try:
            filename = args["filename"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'filename'. Provide the name of the memory file.")
        logger.debug("ReadMemory %s/%s (start_line=%s, end_line=%s)",
                     namespace, filename, args.get("start_line"), args.get("end_line"))

        if namespace not in ("global", "workspace"):
            raise ValueError(f"namespace must be 'global' or 'workspace', got {namespace!r}")
        require_workspace_namespace_accessible(self.context, namespace)

        namespace_dir = memory_namespace_dir(self.context, namespace)
        path = validate_memory_filename(filename, namespace_dir)
        if not path.is_file():
            raise FileNotFoundError(f"no such {namespace} memory: {filename}")

        result = self.read_file_core.apply(path, args)
        result["namespace"] = namespace
        result["filename"] = filename
        logger.debug(
            "ReadMemory %s/%s returned lines %d-%d of %d (truncated=%s)",
            namespace, filename, result["start_line"], result["end_line"], result["total_lines"],
            result["truncated"],
        )
        return result

    def format_response(self, apply_output: Any) -> str:
        return format_read_result(apply_output)

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        namespace = args.get("namespace", "?")
        filename = args.get("filename", "?")
        if error is not None:
            return f"Read memory: {namespace}/{filename} failed: {error}"
        if not isinstance(result, dict):
            return f"Read memory: {namespace}/{filename}"
        return (
            f"Read memory: {result.get('namespace', namespace)}/{result.get('filename', filename)} "
            f"(lines {result.get('start_line')}-{result.get('end_line')} of {result.get('total_lines')})"
        )

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
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
        namespace = args.get("namespace", "?")
        filename = args.get("filename", "?")
        lines = parse_numbered_content(result["content"])
        return ReadPreview(
            label=self.summary(args, result, error), preview_lines=lines[:READ_PREVIEW_MAX_LINES],
            truncated=len(lines) > READ_PREVIEW_MAX_LINES or bool(result.get("truncated")),
            open_full=lambda: self._open_full_view(namespace, filename, result.get("start_line", 1)))

    def _open_full_view(self, namespace: Namespace, filename: str, scroll_to_line: int) -> FullFileView:
        """Re-read the memory in full for the click-to-expand overlay."""
        namespace_dir = memory_namespace_dir(self.context, namespace)
        path = validate_memory_filename(filename, namespace_dir)
        return read_full_file_lines(lambda: self.read_file_core.open_resource(path), scroll_to_line)
