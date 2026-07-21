# © Copyright 2026 Aaron Kimball
"""`TaskSidebar`: a docked, togglable right-hand panel listing this session's chainlink todo
items -- see `klorb.tui.mixins.task_sidebar.TaskSidebarMixin` (Ctrl+T) and
docs/specs/task-sidebar-panel.md.
"""

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

TASK_SIDEBAR_WIDTH = 36
"""Fixed cell width (border-box) for the docked sidebar -- wide enough for a short task title
alongside its `#id` and star marker without eating too much of the history column."""

_HEADER_ID = "task-sidebar-header"
_BODY_ID = "task-sidebar-body"

_HEADER_TEXT = "Tasks"
_EMPTY_MESSAGE = "No tracked tasks."
_UNAVAILABLE_MESSAGE = "Task tracking is not available in this workspace."

_CURRENT_TASK_MARKER = "★ "
"""Prefixed to the session's current tracked task (`Session.cur_chainlink_task_id`) -- a filled
star, `"★"`, so it stands out from the plain two-space indent every other row gets."""
_NOT_CURRENT_TASK_MARKER = "  "

_CLOSED_STYLE = "dim strike"
"""Rich style applied to a closed issue's row -- faded and struck-through, per docs/specs/task-
sidebar-panel.md."""


class TaskSidebar(VerticalScroll, can_focus=False):
    """Lists this session's chainlink todo items (see docs/specs/chainlink-task-tracking.md),
    docked to the right edge of the screen and hidden until `Ctrl+T`
    (`TaskSidebarMixin.action_toggle_task_sidebar`) first shows it. Open issues render plainly;
    closed issues render dim and struck-through (`_CLOSED_STYLE`); whichever issue matches the
    session's `cur_chainlink_task_id` is marked with a leading star
    (`_CURRENT_TASK_MARKER`). `VerticalScroll` (rather than a bare `Static`) so a task list
    taller than the panel scrolls instead of clipping.
    """

    DEFAULT_CSS = f"""
    TaskSidebar {{
        dock: right;
        width: {TASK_SIDEBAR_WIDTH};
        border-left: solid $accent;
        display: none;
    }}
    #{_HEADER_ID} {{
        background: $panel;
        color: $foreground;
        width: 1fr;
        padding: 0 1;
    }}
    #{_BODY_ID} {{
        width: 1fr;
        padding: 0 1;
    }}
    """

    def compose(self) -> ComposeResult:
        yield Static(_HEADER_TEXT, id=_HEADER_ID, markup=False)
        # `markup=False`: an issue title is arbitrary text (chainlink imposes no character
        # restrictions), so a literal `[` in one must render verbatim rather than be parsed as
        # Textual console markup -- fixed at construction time and inherited by every later
        # `update()` call on this same widget (see `Widget.__init__`'s `_render_markup`).
        yield Static(_EMPTY_MESSAGE, id=_BODY_ID, markup=False)

    def show_tasks(self, issues: list[dict[str, Any]], cur_task_id: int | None) -> None:
        """Replace the displayed rows with `issues` (expected already sorted, per
        `klorb.tools.tasks.common.fetch_and_sort_issues`), starring whichever one's `id` matches
        `cur_task_id`."""
        body = self.query_one(f"#{_BODY_ID}", Static)
        if not issues:
            body.update(_EMPTY_MESSAGE)
            return
        text = Text()
        for index, issue in enumerate(issues):
            if index:
                text.append("\n")
            text.append(self._render_issue_line(issue, cur_task_id))
        body.update(text)

    def show_unavailable(self) -> None:
        """Show `_UNAVAILABLE_MESSAGE` in place of a task list -- no `chainlink` binary found,
        or the refresh otherwise failed (see `TaskSidebarMixin._refresh_task_sidebar`)."""
        body = self.query_one(f"#{_BODY_ID}", Static)
        body.update(Text(_UNAVAILABLE_MESSAGE, style="dim"))

    @staticmethod
    def _render_issue_line(issue: dict[str, Any], cur_task_id: int | None) -> Text:
        marker = _CURRENT_TASK_MARKER if issue.get("id") == cur_task_id else _NOT_CURRENT_TASK_MARKER
        label = f"{marker}#{issue.get('id')} {issue.get('title', '')}"
        style = _CLOSED_STYLE if issue.get("status") != "open" else ""
        return Text(label, style=style)
