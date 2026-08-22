# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.tasks.todo_next."""
from collections.abc import Callable
from pathlib import Path

import pytest

from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig, WorkspaceAccess
from klorb.tools.exceptions import ToolCallError
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tasks import todo_next as todo_next_module
from klorb.tools.tasks.common import ALL_LABEL, ChainlinkClient, agent_label, chainlink_available
from klorb.tools.tasks.todo_create import TodoCreateTool
from klorb.tools.tasks.todo_next import TodoNextTool
from klorb.tools.tasks.todo_update import TodoUpdateTool
from klorb.workspace import Workspace

requires_chainlink = pytest.mark.skipif(
    not chainlink_available(),
    reason="chainlink binary not found on PATH or ~/.cargo/bin")


def _context(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig], role_name: str = "operator"
) -> ToolSetupContext:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    config = make_session_config(role_name=role_name, workspace=Workspace(path=workspace_root, trusted=True))
    session = Session(config=config)
    return ToolSetupContext(process_config=ProcessConfig(), session_config=config, session=session)


@requires_chainlink
def test_no_issues_at_all_reports_project_complete(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config)

    result = TodoNextTool(context).apply({})

    assert result == {"work_exists": False, "project_complete": True, "task": None}
    assert context.session is not None
    assert context.session.cur_chainlink_task_id is None


@requires_chainlink
def test_picks_the_only_ready_issue_and_tracks_it(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config)
    created = TodoCreateTool(context).apply({"title": "Only task"})

    result = TodoNextTool(context).apply({})

    assert result["work_exists"] is True
    assert result["project_complete"] is False
    assert result["task"]["id"] == created["id"]
    assert context.session is not None
    assert context.session.cur_chainlink_task_id == created["id"]


@requires_chainlink
def test_open_issues_but_none_ready_reports_no_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    """chainlink itself refuses to create a circular "blocked by" chain (verified against the
    installed binary), so every real, non-empty issue graph under a label has at least one
    ready issue -- this branch is exercised by faking `fetch_and_sort_issues`'s return value
    rather than trying to construct genuinely-impossible chainlink state."""
    context = _context(tmp_path, make_session_config)
    fake_issue = {"id": 1, "status": "open", "priority": "medium", "blocked_by": [2], "labels": []}
    monkeypatch.setattr(
        ChainlinkClient, "fetch_and_sort_issues", lambda self, include_closed: [fake_issue])
    monkeypatch.setattr(
        todo_next_module, "open_blocker_count", lambda issue, open_ids: 1)

    result = TodoNextTool(context).apply({})

    assert result == {"work_exists": True, "project_complete": False, "task": None}
    assert context.session is not None
    assert context.session.cur_chainlink_task_id is None


@requires_chainlink
def test_registers_a_standing_interjection_naming_the_current_task(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config)
    created = TodoCreateTool(context).apply({"title": "Track me"})

    TodoNextTool(context).apply({})

    assert context.session is not None
    message = context.session._standing_interjection_providers["ChainlinkCurrentTask"]()
    assert message is not None
    assert f"#{created['id']}" in message
    assert "Track me" in message


@requires_chainlink
def test_standing_interjection_disappears_once_no_task_is_tracked(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config)
    TodoCreateTool(context).apply({"title": "Only task"})
    TodoNextTool(context).apply({})
    assert context.session is not None
    provider = context.session._standing_interjection_providers["ChainlinkCurrentTask"]
    assert provider() is not None

    context.session.set_chainlink_task(None)

    assert provider() is None


@requires_chainlink
def test_returns_the_same_current_task_again_if_still_open(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config)
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
def test_advances_to_the_next_task_once_the_current_one_is_closed(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """`TodoUpdate`'s close-time auto-advance (see docs/specs/chainlink-task-tracking.md's
    "Auto-activation" section) does the picking itself -- a subsequent `TodoNext` call just
    re-confirms whatever it already picked, the same as it would for any other already-current
    task."""
    context = _context(tmp_path, make_session_config)
    first = TodoCreateTool(context).apply({"title": "First task"})
    second = TodoCreateTool(context).apply({"title": "Second task"})
    TodoNextTool(context).apply({})

    close_result = TodoUpdateTool(context).apply({"id": first["id"], "close": True})

    assert close_result["next_task_id"] == second["id"]
    assert context.session is not None
    assert context.session.cur_chainlink_task_id == second["id"]

    result = TodoNextTool(context).apply({})

    assert result["task"]["id"] == second["id"]
    assert "message" in result


@requires_chainlink
def test_accepts_tasks_capability_gate_raises_before_fetching_anything(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config, role_name="explorer")

    with pytest.raises(ToolCallError, match="may not be assigned tasks"):
        TodoNextTool(context).apply({})


@requires_chainlink
def test_claims_an_all_labeled_ready_issue(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config)
    # activate=False: without it, TodoCreate's own auto-activation would immediately claim this
    # (the only, ready) issue for itself -- see maybe_activate_task's ALL_LABEL-claiming branch --
    # leaving nothing here for TodoNext's own claim path to exercise.
    created = TodoCreateTool(context).apply({
        "title": "Unclaimed task", "assign_to": "all", "activate": False,
    })
    assert context.session is not None
    own_label = agent_label(context.session.id)
    assert ALL_LABEL in created["labels"]
    assert own_label not in created["labels"]

    result = TodoNextTool(context).apply({})

    assert result["task"]["id"] == created["id"]
    claimed = ChainlinkClient(context).show_issue(created["id"])
    assert own_label in claimed["labels"]
    assert ALL_LABEL not in claimed["labels"]


@requires_chainlink
def test_skips_a_ready_issue_labeled_for_a_different_agent(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config)
    created = TodoCreateTool(context).apply({
        "title": "Someone else's task", "assign_to": "all", "activate": False,
    })
    client = ChainlinkClient(context)
    client.remove_label(created["id"], ALL_LABEL)
    client.add_label(created["id"], agent_label("some-other-agent"))

    result = TodoNextTool(context).apply({})

    assert result == {"work_exists": True, "project_complete": False, "task": None}


@requires_chainlink
def test_name_and_parameters(tmp_path: Path, make_session_config: Callable[..., SessionConfig]) -> None:
    tool = TodoNextTool(_context(tmp_path, make_session_config))

    assert tool.name() == "TodoNext"
    assert tool.category() == "TASKS"
    assert tool.is_read_only() is False
    assert tool.parameters()["required"] == []


@requires_chainlink
def test_requires_an_active_session(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = SessionConfig(
        workspace_access=WorkspaceAccess(workspace=Workspace(path=workspace_root, trusted=True)))
    context = ToolSetupContext(process_config=ProcessConfig(), session_config=config, session=None)

    with pytest.raises(ValueError, match="active Session"):
        TodoNextTool(context).apply({})


@requires_chainlink
def test_summary_variants(tmp_path: Path, make_session_config: Callable[..., SessionConfig]) -> None:
    context = _context(tmp_path, make_session_config)
    tool = TodoNextTool(context)

    assert tool.summary({}, {"work_exists": False, "project_complete": True, "task": None}) == (
        "Next todo: all done")
    assert tool.summary({}, {"work_exists": True, "project_complete": False, "task": None}) == (
        "Next todo: nothing ready")
    assert tool.summary({}, error="boom") == "Next todo failed: boom"
