# © Copyright 2026 Aaron Kimball
"""A Tool that deletes an existing memory file for a model."""

import logging
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.tools.memory.common import (
    NAMESPACE_SCHEMA_PROPERTY,
    memory_namespace_dir,
    require_workspace_namespace_accessible,
    validate_memory_filename,
)
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)


class ForgetMemoryTool(Tool):
    """Deletes an existing memory file. Raises `FileNotFoundError` (with a clear message, not a
    raw traceback) if no memory with that `namespace`/`filename` exists.

    `namespace`/`filename` are validated (see `klorb.tools.memory.common.
    validate_memory_filename`) and checked against the untrusted-workspace gate and
    `tools.memory.deletePermission` before any disk I/O -- in that order, so a denied delete
    never touches the filesystem. There is no `writeDirs` check at all, the same bypass as
    every other Memory tool -- see docs/adrs/scratchpad-tools-bypass-permission-tables.md.
    """

    def name(self) -> str:
        return "ForgetMemory"

    def category(self) -> str:
        return "MEMORY"

    def is_read_only(self) -> bool:
        return False

    def description(self) -> str:
        return (
            "Deletes an existing memory file. Fails if no memory with that namespace/filename "
            "exists."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "namespace": NAMESPACE_SCHEMA_PROPERTY,
                "filename": {
                    "type": "string",
                    "description": "Name of the memory file to delete, e.g. 'user-preferences.md'.",
                },
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
        logger.debug("ForgetMemory %s/%s", namespace, filename)

        if namespace not in ("global", "workspace"):
            raise ValueError(f"namespace must be 'global' or 'workspace', got {namespace!r}")
        require_workspace_namespace_accessible(self.context, namespace)
        raise_if_not_allowed(
            self.context.process_config.memory_delete_permission,
            resource_description=f"delete {namespace} memory {filename}")

        namespace_dir = memory_namespace_dir(self.context, namespace)
        path = validate_memory_filename(filename, namespace_dir)
        if not path.is_file():
            raise FileNotFoundError(f"no such {namespace} memory: {filename}")

        path.unlink()
        logger.debug("ForgetMemory %s/%s deleted", namespace, filename)

        return {"namespace": namespace, "filename": filename, "deleted": True}

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        namespace = args.get("namespace", "?")
        filename = args.get("filename", "?")
        if error is not None:
            return f"Forget memory: {namespace}/{filename} failed: {error}"
        return f"Forget memory: {namespace}/{filename}"
