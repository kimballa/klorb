# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.tasks.todo_next."""

from pathlib import Path

import pytest

from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tasks import todo_next as todo_next_module
from klorb.tools.tasks.common import chainlink_available
from klorb.tools.tasks.todo_create import TodoCreateTool
from klorb.tools.tasks.todo_next import TodoNextTool
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
def test_no_issues_at_all_reports_project_complete(tmp_path: Path) -> None:
    context = _context(tmp_path)

    result = TodoNextTool(context).apply({})

    assert result == {"work_exists": False, "project_complete": True, "task": None}
    assert context.session is not None
    assert context.session.cur_chainlink_task_id is None


@requires_chainlink
def test_picks_the_only_ready_issue_and_tracks_it(tmp_path: Path) -> None:
    context = _context(tmp_path)
    created = TodoCreateTool(context).apply({"title": "Only task"})

    result = TodoNextTool(context).apply({})

    assert result["work_exists"] is True
    assert result["project_complete"] is False
    assert result["task"]["id"] == created["id"]
    assert context.session is not None
    assert context.session.cur_chainlink_task_id == created["id"]


@requires_chainlink
def test_open_issues_but_none_ready_reports_no_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chainlink itself refuses to create a circular "blocked by" chain (verified against the
    installed binary), so every real, non-empty issue graph under a label has at least one
    ready issue -- this branch is exercised by faking `fetch_and_sort_issues`'s return value
    rather than trying to construct genuinely-impossible chainlink state."""
    context = _context(tmp_path)
    fake_issue = {"id": 1, "status": "open", "priority": "medium", "blocked_by": [2]}
    monkeypatch.setattr(
        todo_next_module, "fetch_and_sort_issues", lambda client, include_closed: [fake_issue])
    monkeypatch.setattr(
        todo_next_module, "open_blocker_count", lambda issue, open_ids: 1)

    result = TodoNextTool(context).apply({})

    assert result == {"work_exists": True, "project_complete": False, "task": None}
    assert context.session is not None
    assert context.session.cur_chainlink_task_id is None


@requires_chainlink
def test_registers_a_standing_interjection_naming_the_current_task(tmp_path: Path) -> None:
    context = _context(tmp_path)
    created = TodoCreateTool(context).apply({"title": "Track me"})

    TodoNextTool(context).apply({})

    assert context.session is not None
    message = context.session._standing_interjection_providers["ChainlinkCurrentTask"]()
    assert message is not None
    assert f"#{created['id']}" in message
    assert "Track me" in message


@requires_chainlink
def test_standing_interjection_disappears_once_no_task_is_tracked(tmp_path: Path) -> None:
    context = _context(tmp_path)
    TodoCreateTool(context).apply({"title": "Only task"})
    TodoNextTool(context).apply({})
    assert context.session is not None
    provider = context.session._standing_interjection_providers["ChainlinkCurrentTask"]
    assert provider() is not None

    context.session.set_chainlink_task(None)

    assert provider() is None


@requires_chainlink
def test_returns_the_same_current_task_again_if_still_open(tmp_path: Path) -> None:
    context = _context(tmp_path)
    first = TodoCreateTool(context).apply({"title": "First task"})
    TodoCreateTool(context).apply({"title": "Second task"})
    first_result = TodoNextTool(context).apply({})
    assert first_result["task"]["id"] == first["id"]

    second_result = TodoNextTool(context).apply({})

    assert second_result["task"]["id"] == first["id"]
    assert "message" in second_result
    assert f"#{first['id']}" in second_result["message"]
    assert context.session is not None
    assert context.session.cur_chainlink_task_id == first["id"]


@requires_chainlink
def test_advances_to_the_next_task_once_the_current_one_is_closed(tmp_path: Path) -> None:
    context = _context(tmp_path)
    first = TodoCreateTool(context).apply({"title": "First task"})
    second = TodoCreateTool(context).apply({"title": "Second task"})
    TodoNextTool(context).apply({})
    TodoUpdateTool(context).apply({"id": first["id"], "close": True})

    result = TodoNextTool(context).apply({})

    assert result["task"]["id"] == second["id"]
    assert "message" not in result


@requires_chainlink
def test_name_and_parameters(tmp_path: Path) -> None:
    tool = TodoNextTool(_context(tmp_path))

    assert tool.name() == "TodoNext"
    assert tool.category() == "TASKS"
    assert tool.is_read_only() is False
    assert tool.parameters()["required"] == []


@requires_chainlink
def test_requires_an_active_session(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = SessionConfig(workspace=Workspace(path=workspace_root, trusted=True))
    context = ToolSetupContext(process_config=ProcessConfig(), session_config=config, session=None)

    with pytest.raises(ValueError, match="active Session"):
        TodoNextTool(context).apply({})


@requires_chainlink
def test_summary_variants(tmp_path: Path) -> None:
    context = _context(tmp_path)
    tool = TodoNextTool(context)

    assert tool.summary({}, {"work_exists": False, "project_complete": True, "task": None}) == (
        "Next todo: all done")
    assert tool.summary({}, {"work_exists": True, "project_complete": False, "task": None}) == (
        "Next todo: nothing ready")
    assert tool.summary({}, error="boom") == "Next todo failed: boom"
