# © Copyright 2026 Aaron Kimball
"""`SessionMemoryMixin`: the one-shot `Memories` `<SystemInterjection>` body `send_turn()`
prepends onto the first turn's prompt, cataloging the memory topics already on disk."""

import logging
from typing import Any

from klorb.session.mixins._base import SessionBase

logger = logging.getLogger(__name__)


class SessionMemoryMixin(SessionBase):
    """Builds the first-turn `Memories` interjection from `ListMemoriesTool`."""

    def _build_memories_interjection(self) -> str | None:
        """Return the body `send_turn()` wraps in a `<SystemInterjection subject="Memories">`
        tag and prepends onto the first turn's prompt, or `None` if this session has no
        `ToolRegistry`/`ProcessConfig`. Also returns `None` if `ListMemories` itself raises, since
        this is a proactive convenience with no user to ask.
        """
        if self._tool_registry is None or self._process_config is None:
            return None
        try:
            tool = self._tool_registry.instantiate_tool("ListMemories")
            result = tool.apply({})
        except Exception:
            logger.warning("ListMemories for the Memories interjection failed.", exc_info=True)
            return None
        sections = [self._format_memory_topics(
            scope="your user", namespace="global", entries=result.get("global", []))]
        if self.config.workspace.trusted:
            sections.append(self._format_memory_topics(
                scope="this project", namespace="workspace", entries=result.get("workspace", [])))
        sections.append(
            "Use `CreateMemory` and `EditMemory` to improve your memories. Use "
            "`SearchMemories` to search more deeply.")
        return "\n\n".join(sections)

    @staticmethod
    def _format_memory_topics(*, scope: str, namespace: str, entries: list[dict[str, Any]]) -> str:
        """Render one namespace's section of the `Memories` interjection: a bulleted
        `filename: topic` list for `entries` (as returned by `ListMemoriesTool`), or a
        `CreateMemory` nudge if `entries` is empty."""
        if not entries:
            return (
                f"You have not recorded any memories about {scope}. Use "
                f'`CreateMemory(namespace="{namespace}")` to remember something important '
                f"you learn for your next session."
            )
        topics = "\n".join(f"* {entry['filename']}: {entry['topic']}" for entry in entries)
        return (
            f"The following list are memory topics you have recorded about {scope}. Use "
            f'`ReadMemory(namespace="{namespace}")` to read these memories and learn more:\n\n'
            f"{topics}"
        )
