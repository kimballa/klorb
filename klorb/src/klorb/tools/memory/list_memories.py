# © Copyright 2026 Aaron Kimball
"""A Tool that enumerates the memory files available in the global and workspace namespaces."""

import logging
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.tools.memory.common import (
    Namespace,
    memory_namespace_dir,
    read_memory_topic,
    workspace_namespace_accessible,
)
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)


class ListMemoriesTool(Tool):
    """Enumerates every memory file in the `global` and `workspace` namespaces (see
    `klorb.tools.memory.common`), returning each one's `filename` and `topic` (its first line,
    or `""` if the file is empty or that line is blank).

    The `workspace` namespace is reported as `[]` -- without touching disk -- whenever the
    current workspace is untrusted; `global` is unaffected by workspace trust. Gated by
    `tools.memory.readPermission` (`ProcessConfig.memory_read_permission`), the same flag that
    governs `ReadMemory` and `SearchMemories`.
    """

    def name(self) -> str:
        return "ListMemories"

    def category(self) -> str:
        return "MEMORY"

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Lists every memory file in the global namespace (applies across all workspaces) "
            "and the workspace namespace (applies only to the current workspace), reporting "
            "each one's filename and topic (its first line). Use ReadMemory to read one in "
            "full or SearchMemories to find one by keyword."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        logger.debug("ListMemories")
        raise_if_not_allowed(
            self.context.process_config.memory_read_permission, resource_description="list memories")

        result = {
            "global": self._list_namespace("global"),
            "workspace": (
                self._list_namespace("workspace")
                if workspace_namespace_accessible(self.context) else []
            ),
        }
        logger.debug(
            "ListMemories found %d global, %d workspace",
            len(result["global"]), len(result["workspace"]))
        return result

    def _list_namespace(self, namespace: Namespace) -> list[dict[str, str]]:
        namespace_dir = memory_namespace_dir(self.context, namespace)
        if not namespace_dir.is_dir():
            return []
        return [
            {"filename": path.name, "topic": read_memory_topic(path)}
            for path in sorted(namespace_dir.iterdir())
            if path.is_file() and path.suffix == ".md" and not path.name.startswith(".")
        ]

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        if error is not None:
            return f"List memories failed: {error}"
        if not isinstance(result, dict):
            return "List memories"
        return (
            f"List memories ({len(result.get('global', []))} global, "
            f"{len(result.get('workspace', []))} workspace)"
        )
