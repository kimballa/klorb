# © Copyright 2026 Aaron Kimball
"""A Tool that creates a new text file for a model. Refuses to overwrite an existing file."""

import logging
from typing import Any

from klorb.tools._path_safety import resolve_within_workspace
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)


class CreateFileTool(Tool):
    """Creates a new text file with the given content. Raises `FileExistsError` if the file
    already exists — use `EditFile` to modify an existing file's contents instead, so file
    creation is always an explicit, auditable event rather than an implicit side effect of an
    edit. Missing parent directories are created automatically.
    """

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
                "content": {
                    "type": "string",
                    "description": "Contents of the new file. May be an empty string.",
                },
            },
            "required": ["filename", "content"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        filename = args["filename"]
        content = args["content"]
        logger.debug("CreateFile %s (%d bytes)", filename, len(content))

        path = resolve_within_workspace(self.context, filename)
        if path.exists():
            raise FileExistsError(f"{filename} already exists; use EditFile to modify it instead")

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

        total_lines = len(content.splitlines())
        logger.debug("CreateFile %s created (%d lines)", filename, total_lines)

        return {
            "filename": filename,
            "total_lines": total_lines,
            "created": True,
        }
