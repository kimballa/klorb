# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.tasks.todo_create."""

from pathlib import Path

import pytest

from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tasks.common import ChainlinkClient, chainlink_available
from klorb.tools.tasks.todo_create import TodoCreateTool
from klorb.tools.tasks.todo_next import TodoNextTool
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
def test_creates_and_returns_full_issue_detail(tmp_path: Path) -> None:
    context = _context(tmp_path)

    result = TodoCreateTool(context).apply({
        "title": "Write the spec", "description": "details", "priority": "high",
    })

    assert result["title"] == "Write the spec"
    assert result["description"] == "details"
    assert result["priority"] == "high"
    assert result["status"] == "open"


@requires_chainlink
def test_missing_title_raises(tmp_path: Path) -> None:
    context = _context(tmp_path)

    with pytest.raises(ValueError, match="title"):
        TodoCreateTool(context).apply({})


@requires_chainlink
def test_blocked_by_records_dependency(tmp_path: Path) -> None:
    context = _context(tmp_path)
    blocker = TodoCreateTool(context).apply({"title": "Blocker task"})

    blocked = TodoCreateTool(context).apply({
        "title": "Blocked task", "blocked_by": [blocker["id"]],
    })

    assert blocked["blocked_by"] == [blocker["id"]]


@requires_chainlink
def test_blocks_issues_records_the_reverse_dependency(tmp_path: Path) -> None:
    context = _context(tmp_path)
    dependent = TodoCreateTool(context).apply({"title": "Dependent task"})

    new_issue = TodoCreateTool(context).apply({
        "title": "New blocker", "blocks_issues": [dependent["id"]],
    })

    refreshed_dependent = ChainlinkClient(context).show_issue(dependent["id"])
    assert refreshed_dependent["blocked_by"] == [new_issue["id"]]


@requires_chainlink
def test_blocks_current_issue_requires_a_current_task(tmp_path: Path) -> None:
    context = _context(tmp_path)

    with pytest.raises(ValueError, match="no current tracked task"):
        TodoCreateTool(context).apply({"title": "New task", "blocks_current_issue": True})


@requires_chainlink
def test_blocks_current_issue_blocks_the_tracked_task(tmp_path: Path) -> None:
    context = _context(tmp_path)
    TodoCreateTool(context).apply({"title": "Current task"})
    TodoNextTool(context).apply({})
    assert context.session is not None
    current_id = context.session.cur_chainlink_task_id
    assert current_id is not None

    blocker = TodoCreateTool(context).apply({
        "title": "Discovered follow-up", "blocks_current_issue": True,
    })

    current = ChainlinkClient(context).show_issue(current_id)
    assert current["blocked_by"] == [blocker["id"]]


@requires_chainlink
def test_name_and_parameters(tmp_path: Path) -> None:
    tool = TodoCreateTool(_context(tmp_path))

    assert tool.name() == "TodoCreate"
    assert tool.category() == "TASKS"
    assert tool.is_read_only() is False
    assert tool.parameters()["required"] == ["title"]


@requires_chainlink
def test_summary_on_success_and_failure(tmp_path: Path) -> None:
    context = _context(tmp_path)
    tool = TodoCreateTool(context)
    args = {"title": "Write the spec"}

    result = tool.apply(args)

    assert tool.summary(args, result) == f"Create todo: #{result['id']} Write the spec"
    assert tool.summary(args, error="boom") == "Create todo: 'Write the spec' failed: boom"
