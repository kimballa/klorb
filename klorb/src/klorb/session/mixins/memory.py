# © Copyright 2026 Aaron Kimball
"""`SessionMemoryMixin`: the one-shot `Memories` `<SystemInterjection>` body `send_turn()`
prepends onto the first turn's prompt, cataloging the memory topics already on disk and, for
each namespace, the leading content of its `MEMORY.md` table of contents."""

import logging
from typing import Any

from klorb.session.mixins._base import SessionBase
from klorb.tools.memory.common import MEMORY_TOC_AUTO_READ_LINES, MEMORY_TOC_FILENAME, Namespace

logger = logging.getLogger(__name__)


class SessionMemoryMixin(SessionBase):
    """Builds the first-turn `Memories` interjection from `ListMemoriesTool`/`ReadMemoryTool`."""

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
        sections = []
        global_toc = self._read_memory_toc("global")
        if global_toc is not None:
            sections.append(global_toc)
        sections.append(self._format_memory_topics(
            scope="your user", namespace="global", entries=result.get("global", [])))
        if self.config.workspace.trusted:
            workspace_toc = self._read_memory_toc("workspace")
            if workspace_toc is not None:
                sections.append(workspace_toc)
            sections.append(self._format_memory_topics(
                scope="this project", namespace="workspace", entries=result.get("workspace", [])))
        sections.append(
            "Use `CreateMemory` and `EditMemory` to improve your memories. Use "
            "`SearchMemories` to search more deeply.")
        return "\n\n".join(sections)

    def _read_memory_toc(self, namespace: Namespace) -> str | None:
        """Return this namespace's `MEMORY_TOC_FILENAME` section for the Memories interjection --
        its leading `MEMORY_TOC_AUTO_READ_LINES` lines wrapped in a `<MemoryTableOfContents>` tag,
        plus a truncation note if it's longer -- or `None` if it doesn't exist or `ReadMemory`
        fails."""
        assert self._tool_registry is not None
        try:
            tool = self._tool_registry.instantiate_tool("ReadMemory")
            result = tool.apply({
                "namespace": namespace, "filename": MEMORY_TOC_FILENAME,
                "end_line": MEMORY_TOC_AUTO_READ_LINES,
            })
        except FileNotFoundError:
            return None
        except Exception:
            logger.warning(
                "ReadMemory for the Memories interjection's %s %s failed.",
                namespace, MEMORY_TOC_FILENAME, exc_info=True)
            return None
        body = (
            f"Your {namespace} {MEMORY_TOC_FILENAME} (a table of contents over your other "
            f'{namespace} memory files):\n\n<MemoryTableOfContents namespace="{namespace}">\n'
            f"{result['content']}\n</MemoryTableOfContents>"
        )
        if result.get("truncated"):
            body += (
                f"\n\nThis was truncated at {MEMORY_TOC_AUTO_READ_LINES} lines. Use "
                f'`ReadMemory(namespace="{namespace}", filename="{MEMORY_TOC_FILENAME}", '
                f'start_line={MEMORY_TOC_AUTO_READ_LINES + 1})` to see the rest, and consider '
                "using `EditMemory`/`CreateMemory` to move some of it into subject-specific "
                "memory files so the table of contents stays short."
            )
        return body

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
