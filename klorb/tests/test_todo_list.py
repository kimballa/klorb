# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.tasks.todo_list."""

from pathlib import Path

import pytest

from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tasks.common import chainlink_available
from klorb.tools.tasks.todo_create import TodoCreateTool
from klorb.tools.tasks.todo_list import TodoListTool
from klorb.tools.tasks.todo_update import TodoUpdateTool
from klorb.workspace import Workspace

requires_chainlink = pytest.mark.skipif(
    not chainlink_available(),
    reason="chainlink binary not found on PATH or ~/.cargo/bin")


def _context(tmp_path: Path) -> ToolSetupContext:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    config = SessionConfig(workspace=Workspace(path=workspace_root, trusted=True))
    session = Session(config=config)
    return ToolSetupContext(process_config=ProcessConfig(), session_config=config, session=session)


@requires_chainlink
def test_empty_workspace_returns_no_issues(tmp_path: Path) -> None:
    context = _context(tmp_path)

    result = TodoListTool(context).apply({})

    assert result["issues"] == []


@requires_chainlink
def test_lists_open_issues_by_default(tmp_path: Path) -> None:
    context = _context(tmp_path)
    a = TodoCreateTool(context).apply({"title": "A"})
    b = TodoCreateTool(context).apply({"title": "B"})
    TodoUpdateTool(context).apply({"id": b["id"], "close": True})

    result = TodoListTool(context).apply({})

    assert [issue["id"] for issue in result["issues"]] == [a["id"]]


@requires_chainlink
def test_include_closed_returns_closed_issues_sorted_last(tmp_path: Path) -> None:
    context = _context(tmp_path)
    a = TodoCreateTool(context).apply({"title": "A"})
    b = TodoCreateTool(context).apply({"title": "B"})
    TodoUpdateTool(context).apply({"id": b["id"], "close": True})

    result = TodoListTool(context).apply({"include_closed": True})

    ids = [issue["id"] for issue in result["issues"]]
    assert ids == [a["id"], b["id"]]


@requires_chainlink
def test_single_id_uses_show_directly(tmp_path: Path) -> None:
    context = _context(tmp_path)
    created = TodoCreateTool(context).apply({"title": "Solo lookup", "description": "detail"})

    result = TodoListTool(context).apply({"ids": [created["id"]]})

    assert len(result["issues"]) == 1
    assert result["issues"][0]["description"] == "detail"


@requires_chainlink
def test_multiple_ids_filters_the_sorted_result(tmp_path: Path) -> None:
    context = _context(tmp_path)
    a = TodoCreateTool(context).apply({"title": "A"})
    TodoCreateTool(context).apply({"title": "B"})
    c = TodoCreateTool(context).apply({"title": "C"})

    result = TodoListTool(context).apply({"ids": [a["id"], c["id"]]})

    assert {issue["id"] for issue in result["issues"]} == {a["id"], c["id"]}


@requires_chainlink
def test_sorts_unblocked_before_blocked(tmp_path: Path) -> None:
    context = _context(tmp_path)
    blocker = TodoCreateTool(context).apply({"title": "Blocker"})
    blocked = TodoCreateTool(context).apply({
        "title": "Blocked", "blocked_by": [blocker["id"]],
    })
    ready = TodoCreateTool(context).apply({"title": "Ready"})

    result = TodoListTool(context).apply({})

    ids = [issue["id"] for issue in result["issues"]]
    assert ids.index(blocked["id"]) > ids.index(ready["id"])
    assert ids.index(blocker["id"]) < ids.index(blocked["id"])


@requires_chainlink
def test_sorts_by_priority_descending(tmp_path: Path) -> None:
    context = _context(tmp_path)
    low = TodoCreateTool(context).apply({"title": "Low", "priority": "low"})
    critical = TodoCreateTool(context).apply({"title": "Critical", "priority": "critical"})

    result = TodoListTool(context).apply({})

    ids = [issue["id"] for issue in result["issues"]]
    assert ids.index(critical["id"]) < ids.index(low["id"])


@requires_chainlink
def test_a_closed_blockers_blocked_issue_no_longer_counts_as_blocked(tmp_path: Path) -> None:
    """chainlink's own `blocked_by` list is never pruned as a blocker closes -- `TodoList` must
    compute "still in the way" itself (see `klorb.tools.tasks.common.open_blocker_count`)."""
    context = _context(tmp_path)
    blocker = TodoCreateTool(context).apply({"title": "Blocker"})
    blocked = TodoCreateTool(context).apply({
        "title": "Formerly blocked", "blocked_by": [blocker["id"]],
    })
    TodoUpdateTool(context).apply({"id": blocker["id"], "close": True})

    result = TodoListTool(context).apply({})

    ids = [issue["id"] for issue in result["issues"]]
    assert ids == [blocked["id"]]


@requires_chainlink
def test_name_and_parameters(tmp_path: Path) -> None:
    tool = TodoListTool(_context(tmp_path))

    assert tool.name() == "TodoList"
    assert tool.category() == "TASKS"
    assert tool.is_read_only() is True
    assert tool.parameters()["required"] == []


@requires_chainlink
def test_summary(tmp_path: Path) -> None:
    context = _context(tmp_path)
    tool = TodoListTool(context)

    result = tool.apply({})

    assert tool.summary({}, result) == "List todos (0)"
    assert tool.summary({}, error="boom") == "List todos failed: boom"
