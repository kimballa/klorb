# © Copyright 2026 Aaron Kimball
"""TaskSidebarMixin: the Ctrl+T-toggled chainlink task sidebar panel for ReplApp."""

import logging
from typing import Any

from textual import work

from klorb.session import ToolCallEvent
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tasks.common import (
    ChainlinkClient,
    ChainlinkError,
    chainlink_available,
    fetch_and_sort_issues,
)
from klorb.tui._base import ReplAppBase
from klorb.tui.constants import TASK_SIDEBAR_ID
from klorb.tui.widgets.task_sidebar import TaskSidebar

logger = logging.getLogger(__name__)

_TASK_TOOL_NAMES = frozenset({"TodoList", "TodoNext", "TodoCreate", "TodoUpdate"})
"""`ToolCallEvent.name` values that can change the chainlink task list or the session's current
tracked task -- `PromptSubmissionMixin.handle_tool_call` calls
`_maybe_refresh_task_sidebar_after_tool_call` for every finished tool call, and this is what it
checks before bothering to refresh."""


class TaskSidebarMixin(ReplAppBase):
    """Ctrl+T shows or hides a docked right-hand panel listing this session's chainlink todo
    items -- see `klorb.tui.widgets.task_sidebar.TaskSidebar` and
    docs/specs/task-sidebar-panel.md."""

    def action_toggle_task_sidebar(self) -> None:
        """Ctrl+T: show or hide the task sidebar. Showing it triggers an immediate refresh,
        since the list may be stale (or never fetched) from whenever it was last hidden."""
        sidebar = self.query_one(f"#{TASK_SIDEBAR_ID}", TaskSidebar)
        self._task_sidebar_shown = not self._task_sidebar_shown
        sidebar.display = self._task_sidebar_shown
        if self._task_sidebar_shown:
            self._refresh_task_sidebar()

    def _maybe_refresh_task_sidebar_after_tool_call(self, event: ToolCallEvent) -> None:
        """Refresh the task sidebar after a finished tool call that could have changed what it
        shows, but only while it's actually visible -- called from `PromptSubmissionMixin.
        handle_tool_call` for every finished tool call in a turn."""
        if self._task_sidebar_shown and event.name in _TASK_TOOL_NAMES:
            self._refresh_task_sidebar()

    @work(thread=True)
    def _refresh_task_sidebar(self) -> None:
        """Fetch this session's chainlink issues (open and closed) and push them into the
        `TaskSidebar` widget. Runs on a worker thread since `ChainlinkClient` shells out to the
        `chainlink` binary synchronously (see `klorb.tools.tasks.common`); no binary, or any
        `ChainlinkError`/`ValueError` the fetch raises, shows the sidebar's own "unavailable"
        message instead of raising into the UI.
        """
        if not chainlink_available():
            self.call_from_thread(self._show_task_sidebar_unavailable)
            return
        try:
            context = ToolSetupContext(
                process_config=self._process_config, session_config=self._session.config,
                session=self._session)
            client = ChainlinkClient(context)
            issues = fetch_and_sort_issues(client, include_closed=True)
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
