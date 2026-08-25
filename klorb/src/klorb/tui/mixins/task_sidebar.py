# © Copyright 2026 Aaron Kimball
"""TaskSidebarMixin: the Ctrl+T-toggled chainlink task sidebar panel for ReplApp."""

import logging
from typing import Any

from textual import work

from klorb.process_config import persist_sidebar
from klorb.session import ToolCallEvent
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tasks.common import TASK_TOOL_NAMES, ChainlinkClient, ChainlinkError, chainlink_available
from klorb.tui._base import ReplAppBase
from klorb.tui.constants import FILES_PANEL_ID, SUBAGENTS_PANEL_ID, TASK_SIDEBAR_ID
from klorb.tui.widgets.files_panel import FilesPanel
from klorb.tui.widgets.subagents_panel import SubagentsPanel
from klorb.tui.widgets.task_sidebar import TaskSidebar

logger = logging.getLogger(__name__)


class TaskSidebarMixin(ReplAppBase):
    """Ctrl+T shows or hides a docked right-hand panel listing this session's chainlink todo
    items."""

    def action_toggle_task_sidebar(self) -> None:
        """Ctrl+T: show or hide the task sidebar. Showing it triggers an immediate refresh since
        the list may be stale. Mutually exclusive with the subagents panel and the Files panel
        since all three dock the same right-hand slot."""
        sidebar = self.query_one(f"#{TASK_SIDEBAR_ID}", TaskSidebar)
        if self._active_sidebar == "tasks":
            self._active_sidebar = None
            sidebar.display = False
        else:
            if self._active_sidebar == "agents":
                self.query_one(f"#{SUBAGENTS_PANEL_ID}", SubagentsPanel).display = False
            elif self._active_sidebar == "files":
                self.query_one(f"#{FILES_PANEL_ID}", FilesPanel).display = False
            self._active_sidebar = "tasks"
            sidebar.display = True
            self._refresh_task_sidebar()
        persist_sidebar(self._active_sidebar)

    def _maybe_refresh_task_sidebar_after_tool_call(self, event: ToolCallEvent) -> None:
        """Refresh the task sidebar after a finished tool call that could have changed what it
        shows, but only while it's actually visible."""
        if self._active_sidebar == "tasks" and event.name in TASK_TOOL_NAMES:
            self._refresh_task_sidebar()

    @work(thread=True)
    def _refresh_task_sidebar(self) -> None:
        """Fetch this session's chainlink issues and push them into the `TaskSidebar` widget.
        Runs on a worker thread since `ChainlinkClient` shells out to the `chainlink` binary
        synchronously. Shows the sidebar's "unavailable" message if the fetch fails."""
        if not chainlink_available():
            self.call_from_thread(self._show_task_sidebar_unavailable)
            return
        try:
            context = ToolSetupContext(
                process_config=self._process_config, session_config=self._session.config,
                session=self._session)
            client = ChainlinkClient(context)
            issues = client.fetch_and_sort_issues(include_closed=True)
        except (ChainlinkError, ValueError):
            logger.debug("Failed to refresh the task sidebar.", exc_info=True)
            self.call_from_thread(self._show_task_sidebar_unavailable)
            return
        self.call_from_thread(self._show_task_sidebar_issues, issues)

    def _show_task_sidebar_issues(self, issues: list[dict[str, Any]]) -> None:
        sidebar = self.query_one(f"#{TASK_SIDEBAR_ID}", TaskSidebar)
        sidebar.show_tasks(issues, self._session.cur_chainlink_task_id)

    def _show_task_sidebar_unavailable(self) -> None:
        sidebar = self.query_one(f"#{TASK_SIDEBAR_ID}", TaskSidebar)
        sidebar.show_unavailable()
