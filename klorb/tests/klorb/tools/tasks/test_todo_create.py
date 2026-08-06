# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.tasks.todo_create."""

import threading
from pathlib import Path

import pytest

from klorb.agents.runtime import SubagentHandle
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.exceptions import ToolCallError
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tasks.common import ChainlinkClient, chainlink_available
from klorb.tools.tasks.todo_create import TodoCreateTool
from klorb.tools.tasks.todo_list import TodoListTool
from klorb.tools.tasks.todo_next import TodoNextTool
from klorb.workspace import Workspace

requires_chainlink = pytest.mark.skipif(
    not chainlink_available(),
    reason="chainlink binary not found on PATH or ~/.cargo/bin")


def _context(tmp_path: Path, role_name: str = "operator") -> ToolSetupContext:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    config = SessionConfig(role_name=role_name, workspace=Workspace(path=workspace_root, trusted=True))
    session = Session(config=config)
    return ToolSetupContext(process_config=ProcessConfig(), session_config=config, session=session)


def _child_context(parent_context: ToolSetupContext) -> ToolSetupContext:
    """A second `ToolSetupContext` for a real subagent of `parent_context`'s session -- sharing
    the same workspace/root, registered on the parent's `subagent_tracker` so `find_session_in_group`
    can resolve it, for tests exercising `assign_to` against another agent."""
    parent_session = parent_context.session
    assert parent_session is not None
    child_session = Session(
        config=parent_context.session_config.model_copy(deep=True), parent=parent_session,
        root_id=parent_session.root_id)
    handle = SubagentHandle(
        session=child_session, thread=threading.Thread(target=lambda: None),
        cancel_event=threading.Event(), role=child_session.config.role_name, title="child")
    parent_session.subagent_tracker.register(handle)
    return ToolSetupContext(
        process_config=parent_context.process_config, session_config=child_session.config,
        session=child_session)


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

    # Validated up front, before creating anything -- no orphaned issue left behind.
    assert TodoListTool(context).apply({"include_closed": True})["issues"] == []


@requires_chainlink
def test_invalid_priority_rejected_before_creating_anything(tmp_path: Path) -> None:
    context = _context(tmp_path)

    with pytest.raises(ValueError, match="priority must be one of"):
        TodoCreateTool(context).apply({"title": "New task", "priority": "urgent"})

    assert TodoListTool(context).apply({"include_closed": True})["issues"] == []


@requires_chainlink
def test_failed_dependency_recording_closes_the_new_issue_with_an_explanatory_comment(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)

    with pytest.raises(Exception, match="not found"):
        TodoCreateTool(context).apply({"title": "New task", "blocked_by": [999999]})

    issues = TodoListTool(context).apply({"include_closed": True})["issues"]
    assert len(issues) == 1
    assert issues[0]["status"] == "closed"
    assert "Created in error" in issues[0]["comments"][-1]["content"]


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
def test_auto_activates_as_current_task_when_none_is_set(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert context.session is not None

    result = TodoCreateTool(context).apply({"title": "New task"})

    assert context.session.cur_chainlink_task_id == result["id"]
    assert f"#{result['id']}" in result["active_task_note"]


@requires_chainlink
def test_auto_activation_skipped_when_a_current_task_already_exists(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert context.session is not None
    first = TodoCreateTool(context).apply({"title": "First"})
    assert context.session.cur_chainlink_task_id == first["id"]

    second = TodoCreateTool(context).apply({"title": "Second"})

    assert context.session.cur_chainlink_task_id == first["id"]
    assert "active_task_note" not in second


@requires_chainlink
def test_activate_true_forces_activation_over_an_existing_current_task(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert context.session is not None
    first = TodoCreateTool(context).apply({"title": "First"})
    assert context.session.cur_chainlink_task_id == first["id"]

    second = TodoCreateTool(context).apply({"title": "Second", "activate": True})

    assert context.session.cur_chainlink_task_id == second["id"]
    assert "active_task_note" in second


@requires_chainlink
def test_activate_false_suppresses_auto_activation(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert context.session is not None

    result = TodoCreateTool(context).apply({"title": "New task", "activate": False})

    assert context.session.cur_chainlink_task_id is None
    assert "active_task_note" not in result


@requires_chainlink
def test_auto_activation_skipped_when_the_new_task_is_not_ready(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert context.session is not None
    blocker = TodoCreateTool(context).apply({"title": "Blocker", "activate": False})
    assert context.session.cur_chainlink_task_id is None

    blocked = TodoCreateTool(context).apply({
        "title": "Blocked task", "blocked_by": [blocker["id"]],
    })

    assert context.session.cur_chainlink_task_id is None
    assert "active_task_note" not in blocked


@requires_chainlink
def test_assign_to_defaults_to_self_label(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert context.session is not None

    result = TodoCreateTool(context).apply({"title": "New task"})

    assert f"agent:{context.session.id}" in result["labels"]


@requires_chainlink
def test_assign_to_all_labels_it_unclaimed(tmp_path: Path) -> None:
    context = _context(tmp_path)

    # activate=False: otherwise auto-activation would immediately claim this (the only, ready)
    # issue for the creating session -- see maybe_activate_task's ALL_LABEL-claiming branch --
    # leaving nothing "all"-labeled to assert on.
    result = TodoCreateTool(context).apply({
        "title": "New task", "assign_to": "all", "activate": False,
    })

    assert "all" in result["labels"]


@requires_chainlink
def test_assign_to_all_is_claimed_by_auto_activation_when_ready(tmp_path: Path) -> None:
    """`maybe_activate_task` claims an `ALL_LABEL` task for this session at the moment it becomes
    the current tracked task -- otherwise it would stay up for grabs for another agent's
    `TodoNext` even while this session already treats it as current."""
    context = _context(tmp_path)
    assert context.session is not None

    result = TodoCreateTool(context).apply({"title": "New task", "assign_to": "all"})

    assert f"agent:{context.session.id}" in result["labels"]
    assert "all" not in result["labels"]
    assert context.session.cur_chainlink_task_id == result["id"]


@requires_chainlink
def test_assign_to_self_without_accepts_tasks_raises(tmp_path: Path) -> None:
    context = _context(tmp_path, role_name="explorer")

    with pytest.raises(ToolCallError, match="may not be assigned tasks"):
        TodoCreateTool(context).apply({"title": "New task"})

    assert TodoListTool(context).apply({"include_closed": True})["issues"] == []


@requires_chainlink
def test_assign_to_another_agent_requires_assigns_tasks_capability(tmp_path: Path) -> None:
    context = _context(tmp_path, role_name="explorer")

    with pytest.raises(ToolCallError, match="may not assign tasks to other agents"):
        TodoCreateTool(context).apply({"title": "New task", "assign_to": "some-other-agent"})


@requires_chainlink
def test_assign_to_unknown_agent_id_raises(tmp_path: Path) -> None:
    context = _context(tmp_path)

    with pytest.raises(ToolCallError, match="No such agent in this group"):
        TodoCreateTool(context).apply({"title": "New task", "assign_to": "no-such-agent"})


@requires_chainlink
def test_assign_to_another_agent_labels_it_for_them(tmp_path: Path) -> None:
    parent_context = _context(tmp_path)
    child_context = _child_context(parent_context)
    assert child_context.session is not None

    result = TodoCreateTool(parent_context).apply({
        "title": "For the subagent", "assign_to": child_context.session.id,
    })

    assert f"agent:{child_context.session.id}" in result["labels"]


@requires_chainlink
def test_assign_to_another_agent_requires_the_target_to_accept_tasks(tmp_path: Path) -> None:
    parent_context = _context(tmp_path)
    child_context = _child_context(parent_context)
    assert child_context.session is not None
    child_context.session_config.role_name = "explorer"

    with pytest.raises(ToolCallError, match="may not be assigned"):
        TodoCreateTool(parent_context).apply({
            "title": "For the subagent", "assign_to": child_context.session.id,
        })


@requires_chainlink
def test_todo_write_is_an_alias_for_todo_create(tmp_path: Path) -> None:
    tool = TodoCreateTool(_context(tmp_path))

    assert "TodoWrite" in (tool.aliases() or [])


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
