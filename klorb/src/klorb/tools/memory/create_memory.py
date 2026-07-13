# © Copyright 2026 Aaron Kimball
"""A Tool that creates a new memory file for a model. Refuses to overwrite an existing one."""

import logging
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.tools.memory.common import (
    NAMESPACE_SCHEMA_PROPERTY,
    memory_namespace_dir,
    require_workspace_namespace_accessible,
    validate_first_line_not_blank,
    validate_memory_filename,
)
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool
from klorb.tools.util import CreateFileCore

logger = logging.getLogger(__name__)


class CreateMemoryTool(Tool):
    """Creates a new memory file with the given content. Raises `FileExistsError` if a memory
    with that `namespace`/`filename` already exists — use `EditMemory` to modify one instead.
    Delegates the file-creation mechanic to `self.create_file_core` (a
    `klorb.tools.util.CreateFileCore`), the same one `CreateFileTool` uses; missing parent
    directories (including the namespace directory itself, on its very first write) are
    created automatically.

    A memory's first line is its topic (see docs/specs/memories.md's file-format rule) and must
    never be blank, so `content` is validated up front, before any disk I/O -- there is no way
    to create a memory with no topic and fill it in later via `EditMemory`.

    `namespace`/`filename` are validated (see `klorb.tools.memory.common.
    validate_memory_filename`) and checked against the untrusted-workspace gate and
    `tools.memory.createPermission` before any disk I/O.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self.create_file_core = CreateFileCore()

    def name(self) -> str:
        return "CreateMemory"

    def description(self) -> str:
        return (
            "Creates a new memory file with the given content. Fails if a memory with that "
            "namespace/filename already exists -- use EditMemory to modify one instead. "
            "content's first line is the memory's topic and must not be blank."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "namespace": NAMESPACE_SCHEMA_PROPERTY,
                "filename": {
                    "type": "string",
                    "description": "Name of the new memory file to create, e.g. 'user-preferences.md'.",
                },
                **self.create_file_core.parameter_properties(),
            },
            "required": ["namespace", "filename", "content"],
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
                "Missing required argument: 'filename'. Provide the name of the memory file to "
                "create.")
        try:
            content = args["content"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'content'. Provide the contents for the new memory "
                "file (may be an empty string).")
        logger.debug("CreateMemory %s/%s (%d bytes)", namespace, filename, len(content))

        if namespace not in ("global", "workspace"):
            raise ValueError(f"namespace must be 'global' or 'workspace', got {namespace!r}")
        require_workspace_namespace_accessible(self.context, namespace)
        raise_if_not_allowed(
            self.context.process_config.memory_create_permission,
            resource_description=f"create {namespace} memory {filename}")

        subject = f"{namespace} memory {filename}"
        validate_first_line_not_blank(args["content"], subject=subject)

        namespace_dir = memory_namespace_dir(self.context, namespace)
        path = validate_memory_filename(filename, namespace_dir)

        result = self.create_file_core.apply(path, args, subject=subject, edit_hint="EditMemory")
        result["namespace"] = namespace
        result["filename"] = filename

        logger.debug("CreateMemory %s/%s created (%d lines)", namespace, filename, result["total_lines"])
        return result

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        namespace = args.get("namespace", "?")
        filename = args.get("filename", "?")
        if error is not None:
            return f"Create memory: {namespace}/{filename} failed: {error}"
        if not isinstance(result, dict):
            return f"Create memory: {namespace}/{filename}"
        return (
            f"Create memory: {result.get('namespace', namespace)}/{result.get('filename', filename)} "
            f"({result.get('total_lines')} lines)"
        )
