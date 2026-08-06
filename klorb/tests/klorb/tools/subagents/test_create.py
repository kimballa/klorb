# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.subagents.create.CreateSubagentTool."""

from pathlib import Path

import pytest
from tools.subagents.conftest import _FakeProvider

from klorb.agents.policy import compute_root_session_grants
from klorb.api_provider import ApiProvider
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.exceptions import ToolCallError
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.subagents import create as create_module
from klorb.tools.subagents.create import CreateSubagentTool
from klorb.tools.tasks.common import chainlink_available
from klorb.workspace import Workspace

requires_chainlink = pytest.mark.skipif(
    not chainlink_available(),
    reason="chainlink binary not found on PATH or ~/.cargo/bin")


def _operator_context(tmp_path: Path, provider: ApiProvider) -> ToolSetupContext:
    process_config = ProcessConfig()
    session_config = SessionConfig(role_name="operator", workspace=Workspace(path=tmp_path))
    grants = compute_root_session_grants(process_config, session_config, session_config.role_name)
    session_config.skill_rules = grants.skill_rules
    session = Session(
        session_config, provider=provider, process_config=process_config,
        tool_registry=grants.tool_registry, effective_subagent_roles=grants.effective_subagent_roles)
    return ToolSetupContext(process_config=process_config, session_config=session_config, session=session)


def test_apply_returns_a_subagent_id_and_note(tmp_path: Path) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider)
    assert context.session is not None
    tool = CreateSubagentTool(context)

    result = tool.apply({
        "role": "explorer", "session_title": "find the bug", "initial_message": "look for it",
    })

    assert isinstance(result, dict)
    assert result["subagent_id"]
    assert "note" in result
    context.session.subagent_tracker.handles()[0].thread.join(timeout=5.0)


def test_apply_registers_the_subagent_and_delivers_its_output(tmp_path: Path) -> None:
    provider = _FakeProvider(reply_text="found it in foo.py")
    context = _operator_context(tmp_path, provider)
    assert context.session is not None
    tool = CreateSubagentTool(context)

    result = tool.apply({
        "role": "explorer", "session_title": "find the bug", "initial_message": "look for it",
    })

    handles = context.session.subagent_tracker.handles()
    assert len(handles) == 1
    handle = handles[0]
    assert handle.session.id == result["subagent_id"]
    assert handle.role == "explorer"
    assert handle.title == "find the bug"
    handle.thread.join(timeout=5.0)
    assert not handle.thread.is_alive()
    assert handle.state == "finished"
    assert handle.output == "found it in foo.py"


def test_subagent_session_gets_the_explorer_role_and_scratchpad_path(tmp_path: Path) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider)
    assert context.session is not None
    tool = CreateSubagentTool(context)

    tool.apply({"role": "explorer", "session_title": "task", "initial_message": "go"})

    handle = context.session.subagent_tracker.handles()[0]
    assert handle.session.config.role_name == "explorer"
    assert handle.session.parent is context.session
    assert handle.session.depth == 1
    assert handle.session.scratchpad.path == context.session.scratchpad.path
    assert handle.session.name == "task"
    handle.thread.join(timeout=5.0)


def test_subagent_session_shares_the_parents_root_id(tmp_path: Path) -> None:
    """A subagent's `root_id` must match its creator's, not default to its own `id` -- otherwise
    it would scope its chainlink issues to a group of its own rather than sharing its creator's
    (see `Session.get_chainlink_label()` and docs/specs/chainlink-task-tracking.md's "Session
    state" section)."""
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider)
    assert context.session is not None
    tool = CreateSubagentTool(context)

    tool.apply({"role": "explorer", "session_title": "task", "initial_message": "go"})

    handle = context.session.subagent_tracker.handles()[0]
    assert handle.session.root_id == context.session.root_id
    assert handle.session.get_chainlink_label() == context.session.get_chainlink_label()
    handle.thread.join(timeout=5.0)


def test_apply_does_not_touch_chainlink_when_child_has_no_task_tool(tmp_path: Path) -> None:
    """Explorer (the only launchable role today) has no `TASKS` tool in its resolved tool set, so
    `CreateSubagentTool.apply()` must not construct a `ChainlinkClient` at all -- constructing one
    for a fresh workspace runs `chainlink init`, too expensive to pay for every subagent creation
    regardless of whether it could ever track tasks."""
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider)
    assert context.session is not None
    tool = CreateSubagentTool(context)

    tool.apply({"role": "explorer", "session_title": "task", "initial_message": "go"})

    assert context.session._chainlink_client_ensured is False
    context.session.subagent_tracker.handles()[0].thread.join(timeout=5.0)


@requires_chainlink
def test_apply_ensures_the_creators_chainlink_client_when_child_has_a_task_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explorer has no real `TASKS` tool today, so this patches `TASK_TOOL_NAMES` to overlap with
    a tool Explorer definitely has (`ReadFile`), purely to exercise the
    `ensure_chainlink_client()` wiring without a real task-tracking subagent role existing yet."""
    monkeypatch.setattr(create_module, "TASK_TOOL_NAMES", frozenset({"ReadFile"}))
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider)
    assert context.session is not None
    tool = CreateSubagentTool(context)

    tool.apply({"role": "explorer", "session_title": "task", "initial_message": "go"})

    assert context.session._chainlink_client_ensured is True
    assert "ChainlinkClient" in context.session._teardown_callbacks
    context.session.subagent_tracker.handles()[0].thread.join(timeout=5.0)


def test_apply_raises_without_constructing_a_session_when_validation_fails(tmp_path: Path) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider)
    assert context.session is not None
    tool = CreateSubagentTool(context)

    with pytest.raises(ToolCallError):
        tool.apply({"role": "no_such_role", "session_title": "t", "initial_message": "m"})

    assert context.session.subagent_tracker.handles() == []


def test_name_and_category(tmp_path: Path) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider)
    tool = CreateSubagentTool(context)

    assert tool.name() == "CreateSubagent"
    assert tool.category() == "SUBAGENT"
    assert tool.is_read_only() is True
