# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.tasks.todo_update."""

from pathlib import Path

import pytest

from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tasks.common import chainlink_available
from klorb.tools.tasks.todo_create import TodoCreateTool
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
def test_missing_id_raises(tmp_path: Path) -> None:
    context = _context(tmp_path)

    with pytest.raises(ValueError, match="'id'"):
        TodoUpdateTool(context).apply({})


@requires_chainlink
def test_updates_title_description_and_priority(tmp_path: Path) -> None:
    context = _context(tmp_path)
    created = TodoCreateTool(context).apply({"title": "Original"})

    result = TodoUpdateTool(context).apply({
        "id": created["id"], "new_title": "Renamed", "new_description": "new desc",
        "new_priority": "critical",
    })

    assert result["title"] == "Renamed"
    assert result["description"] == "new desc"
    assert result["priority"] == "critical"


@requires_chainlink
def test_close_does_not_write_a_changelog_file(tmp_path: Path) -> None:
    context = _context(tmp_path)
    created = TodoCreateTool(context).apply({"title": "Task"})

    result = TodoUpdateTool(context).apply({"id": created["id"], "close": True})

    assert result["status"] == "closed"
    assert not (context.session_config.workspace.path / "CHANGELOG.md").exists()


@requires_chainlink
def test_close_clears_cur_chainlink_task_id_when_it_is_the_tracked_task(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert context.session is not None
    created = TodoCreateTool(context).apply({"title": "Task"})
    context.session.set_chainlink_task(created["id"])

    TodoUpdateTool(context).apply({"id": created["id"], "close": True})

    assert context.session.cur_chainlink_task_id is None


@requires_chainlink
def test_close_leaves_cur_chainlink_task_id_alone_for_a_different_task(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert context.session is not None
    tracked = TodoCreateTool(context).apply({"title": "Tracked"})
    other = TodoCreateTool(context).apply({"title": "Other"})
    context.session.set_chainlink_task(tracked["id"])

    TodoUpdateTool(context).apply({"id": other["id"], "close": True})

    assert context.session.cur_chainlink_task_id == tracked["id"]


@requires_chainlink
def test_close_auto_advances_to_the_next_ready_task(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert context.session is not None
    first = TodoCreateTool(context).apply({"title": "First"})
    second = TodoCreateTool(context).apply({"title": "Second", "activate": False})
    assert context.session.cur_chainlink_task_id == first["id"]

    result = TodoUpdateTool(context).apply({"id": first["id"], "close": True})

    assert result["next_task_id"] == second["id"]
    assert result["next_task_title"] == "Second"
    assert f"#{second['id']}" in result["active_task_note"]
    assert context.session.cur_chainlink_task_id == second["id"]


@requires_chainlink
def test_close_reports_no_next_task_when_nothing_remains(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert context.session is not None
    created = TodoCreateTool(context).apply({"title": "Only task"})

    result = TodoUpdateTool(context).apply({"id": created["id"], "close": True})

    assert result["next_task_id"] is None
    assert result["next_task_title"] is None
    assert "active_task_note" in result
    assert context.session.cur_chainlink_task_id is None


@requires_chainlink
def test_close_with_activate_false_suppresses_auto_advance(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert context.session is not None
    first = TodoCreateTool(context).apply({"title": "First"})
    TodoCreateTool(context).apply({"title": "Second", "activate": False})
    assert context.session.cur_chainlink_task_id == first["id"]

    result = TodoUpdateTool(context).apply({
        "id": first["id"], "close": True, "activate": False,
    })

    assert "next_task_id" not in result
    assert "active_task_note" not in result
    assert context.session.cur_chainlink_task_id is None


@requires_chainlink
def test_reopen_a_closed_issue(tmp_path: Path) -> None:
    context = _context(tmp_path)
    created = TodoCreateTool(context).apply({"title": "Task"})
    TodoUpdateTool(context).apply({"id": created["id"], "close": True})

    result = TodoUpdateTool(context).apply({"id": created["id"], "reopen": True})

    assert result["status"] == "open"


@requires_chainlink
def test_add_comment(tmp_path: Path) -> None:
    context = _context(tmp_path)
    created = TodoCreateTool(context).apply({"title": "Task"})

    result = TodoUpdateTool(context).apply({"id": created["id"], "add_comment": "made progress"})

    assert result["comments"][0]["content"] == "made progress"


@requires_chainlink
def test_depends_on_and_drop_dependency(tmp_path: Path) -> None:
    context = _context(tmp_path)
    blocker = TodoCreateTool(context).apply({"title": "Blocker"})
    task = TodoCreateTool(context).apply({"title": "Task"})

    TodoUpdateTool(context).apply({"id": task["id"], "depends_on": [blocker["id"]]})
    added = TodoUpdateTool(context).apply({"id": task["id"]})
    assert added["blocked_by"] == [blocker["id"]]

    removed = TodoUpdateTool(context).apply({"id": task["id"], "drop_dependency": [blocker["id"]]})
    assert removed["blocked_by"] == []


@requires_chainlink
def test_auto_activates_as_current_task_when_none_is_set(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert context.session is not None
    created = TodoCreateTool(context).apply({"title": "Task", "activate": False})
    assert context.session.cur_chainlink_task_id is None

    result = TodoUpdateTool(context).apply({"id": created["id"], "add_comment": "progress"})

    assert context.session.cur_chainlink_task_id == created["id"]
    assert f"#{created['id']}" in result["active_task_note"]


@requires_chainlink
def test_auto_activation_skipped_when_a_current_task_already_exists(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert context.session is not None
    tracked = TodoCreateTool(context).apply({"title": "Tracked"})
    other = TodoCreateTool(context).apply({"title": "Other", "activate": False})
    assert context.session.cur_chainlink_task_id == tracked["id"]

    result = TodoUpdateTool(context).apply({"id": other["id"], "add_comment": "progress"})

    assert context.session.cur_chainlink_task_id == tracked["id"]
    assert "active_task_note" not in result


@requires_chainlink
def test_activate_true_forces_activation_over_an_existing_current_task(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert context.session is not None
    tracked = TodoCreateTool(context).apply({"title": "Tracked"})
    other = TodoCreateTool(context).apply({"title": "Other", "activate": False})
    assert context.session.cur_chainlink_task_id == tracked["id"]

    result = TodoUpdateTool(context).apply({"id": other["id"], "activate": True})

    assert context.session.cur_chainlink_task_id == other["id"]
    assert "active_task_note" in result


@requires_chainlink
def test_activate_false_suppresses_auto_activation(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert context.session is not None
    created = TodoCreateTool(context).apply({"title": "Task", "activate": False})

    result = TodoUpdateTool(context).apply({
        "id": created["id"], "add_comment": "progress", "activate": False,
    })

    assert context.session.cur_chainlink_task_id is None
    assert "active_task_note" not in result


@requires_chainlink
def test_auto_activation_skipped_when_the_task_is_not_ready(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert context.session is not None
    blocker = TodoCreateTool(context).apply({"title": "Blocker", "activate": False})
    task = TodoCreateTool(context).apply({"title": "Task", "activate": False})

    result = TodoUpdateTool(context).apply({"id": task["id"], "depends_on": [blocker["id"]]})

    assert context.session.cur_chainlink_task_id is None
    assert "active_task_note" not in result


@requires_chainlink
def test_close_applies_after_a_comment_added_in_the_same_call(tmp_path: Path) -> None:
    context = _context(tmp_path)
    created = TodoCreateTool(context).apply({"title": "Task"})

    result = TodoUpdateTool(context).apply({
        "id": created["id"], "add_comment": "final note", "close": True,
    })

    assert result["status"] == "closed"
    assert result["comments"][-1]["content"] == "final note"


@requires_chainlink
def test_name_and_parameters(tmp_path: Path) -> None:
    tool = TodoUpdateTool(_context(tmp_path))

    assert tool.name() == "TodoUpdate"
    assert tool.category() == "TASKS"
    assert tool.is_read_only() is False
    assert tool.parameters()["required"] == ["id"]


@requires_chainlink
def test_summary_on_success_and_failure(tmp_path: Path) -> None:
    context = _context(tmp_path)
    created = TodoCreateTool(context).apply({"title": "Task"})
    tool = TodoUpdateTool(context)
    args = {"id": created["id"]}

    result = tool.apply(args)

    assert tool.summary(args, result) == f"Update todo #{created['id']} Task"
    assert tool.summary(args, error="boom") == f"Update todo #{created['id']} failed: boom"


@requires_chainlink
def test_summary_mentions_which_fields_were_updated(tmp_path: Path) -> None:
    context = _context(tmp_path)
    created = TodoCreateTool(context).apply({"title": "Task"})
    tool = TodoUpdateTool(context)
    args = {"id": created["id"], "new_priority": "high", "close": True}

    result = tool.apply(args)

    assert tool.summary(args, result) == (
        f"Update todo #{created['id']} Task (priority, closed)")


@requires_chainlink
def test_invalid_new_priority_is_rejected(tmp_path: Path) -> None:
    context = _context(tmp_path)
    created = TodoCreateTool(context).apply({"title": "Task"})

    with pytest.raises(ValueError, match="priority must be one of"):
        TodoUpdateTool(context).apply({"id": created["id"], "new_priority": "urgent"})
