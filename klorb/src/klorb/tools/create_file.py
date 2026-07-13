# © Copyright 2026 Aaron Kimball
"""A Tool that creates a new text file for a model. Refuses to overwrite an existing file."""

import logging
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import resolve_and_evaluate_write
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

    `filename` is checked against `writeFiles` (an exact-match carve-out, checked first — see
    `klorb.permissions.workspace.resolve_and_evaluate_write`) and otherwise confined to
    `SessionConfig.workspace.path` and further checked against `writeDirs` before any disk I/O.
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
        try:
            filename = args["filename"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'filename'. Provide the path of the file to create.")
        try:
            content = args["content"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'content'. Provide the contents for the new file "
                "(may be an empty string).")
        logger.debug("CreateFile %s (%d bytes)", filename, len(content))

        path, verdict = resolve_and_evaluate_write(self.context, filename)
        raise_if_not_allowed(
            verdict, resource_description=f"write to {path}", path=path, is_write=True)

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
