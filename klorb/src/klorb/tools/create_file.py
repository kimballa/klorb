# © Copyright 2026 Aaron Kimball
"""A Tool that creates a new text file for a model. Refuses to overwrite an existing file."""

import logging
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import evaluate_write, resolve_within_workspace
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool
from klorb.tools.util import CreateFileCore

logger = logging.getLogger(__name__)


class CreateFileTool(Tool):
    """Creates a new text file with the given content. Raises `FileExistsError` if the file
    already exists — use `EditFile` to modify an existing file's contents instead, so file
    creation is always an explicit, auditable event rather than an implicit side effect of an
    edit. Missing parent directories are created automatically. Delegates the file-creation
    mechanic to `self.create_file_core` (a `klorb.tools.util.CreateFileCore`), the same one
    `CreateMemoryTool` uses.

    `filename` is confined to `SessionConfig.workspace.path` and further checked against
    `writeDirs` (see `klorb.permissions.workspace.evaluate_write`) before any disk I/O.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self.create_file_core = CreateFileCore()

    def name(self) -> str:
        return "CreateFile"

    def description(self) -> str:
        return (
            "Creates a new text file with the given content. Fails if the file already "
            "exists — use EditFile to modify an existing file instead. Missing parent "
            "directories are created automatically."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Path of the new text file to create.",
                },
                **self.create_file_core.parameter_properties(),
            },
            "required": ["filename", "content"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        filename = args["filename"]
        logger.debug("CreateFile %s (%d bytes)", filename, len(args["content"]))

        path = resolve_within_workspace(self.context, filename)
        raise_if_not_allowed(
            evaluate_write(self.context, path), resource_description=f"write to {path}",
            path=path, is_write=True)

        result = self.create_file_core.apply(path, args, subject=filename, edit_hint="EditFile")
        result["filename"] = filename

        logger.debug("CreateFile %s created (%d lines)", filename, result["total_lines"])
        return result

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        filename = args.get("filename", "?")
        if error is not None:
            return f"Create file: {filename} failed: {error}"
        if not isinstance(result, dict):
            return f"Create file: {filename}"
        return f"Create file: {result.get('filename', filename)} ({result.get('total_lines')} lines)"
