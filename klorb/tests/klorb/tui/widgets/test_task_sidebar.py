# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.widgets.task_sidebar."""

from typing import Any

from klorb.tui.widgets.task_sidebar import TaskSidebar


def _issue(issue_id: int, title: str, status: str = "open") -> dict[str, Any]:
    return {"id": issue_id, "title": title, "status": status, "blocked_by": []}


def test_render_issue_line_open_task_is_unstyled_and_unstarred() -> None:
    line = TaskSidebar._render_issue_line(_issue(1, "Write the spec"), cur_task_id=None)

    assert str(line) == "  #1 Write the spec"
    assert line.style == ""


def test_render_issue_line_closed_task_is_dim_and_struck_through() -> None:
    line = TaskSidebar._render_issue_line(_issue(1, "Write the spec", status="closed"), cur_task_id=None)

    assert str(line) == "  #1 Write the spec"
    assert line.style == "dim strike"


def test_render_issue_line_current_task_is_starred() -> None:
    line = TaskSidebar._render_issue_line(_issue(2, "In progress"), cur_task_id=2)

    assert str(line) == "★ #2 In progress"


def test_render_issue_line_non_current_task_is_not_starred() -> None:
    line = TaskSidebar._render_issue_line(_issue(1, "Other task"), cur_task_id=2)

    assert str(line) == "  #1 Other task"
