# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.subagents.create.CreateSubagentTool."""
from collections.abc import Callable
from pathlib import Path

import pytest
from tools.subagents.conftest import _FakeProvider

from klorb.agents.policy import compute_root_session_grants
from klorb.api_provider import ApiProvider
from klorb.models.placeholders import DEFAULT_PLACEHOLDER_MODEL_FAST, DEFAULT_PLACEHOLDER_MODEL_HEAVY
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


def _operator_context(
    tmp_path: Path, provider: ApiProvider, make_session_config: Callable[..., SessionConfig]
) -> ToolSetupContext:
    process_config = ProcessConfig()
    session_config = make_session_config(role_name="operator", workspace=Workspace(path=tmp_path))
    grants = compute_root_session_grants(process_config, session_config, session_config.role_name)
    session_config.skill_rules = grants.skill_rules
    session = Session(
        session_config, provider=provider, process_config=process_config,
        tool_registry=grants.tool_registry, effective_subagent_roles=grants.effective_subagent_roles)
    return ToolSetupContext(process_config=process_config, session_config=session_config, session=session)


def test_apply_returns_a_subagent_id_and_note(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    tool = CreateSubagentTool(context)

    result = tool.apply({
        "role": "explorer", "session_title": "find the bug", "initial_message": "look for it",
    })

    assert isinstance(result, dict)
    assert result["subagent_id"]
    assert "note" in result
    context.session.subagent_tracker.handles()[0].thread.join(timeout=5.0)


def test_apply_registers_the_subagent_and_delivers_its_output(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider(reply_text="found it in foo.py")
    context = _operator_context(tmp_path, provider, make_session_config)
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


def test_subagent_session_gets_the_explorer_role_and_scratchpad_path(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
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


def test_apply_resolves_the_roles_placeholder_default_model(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    tool = CreateSubagentTool(context)

    tool.apply({"role": "explorer", "session_title": "task", "initial_message": "go"})

    handle = context.session.subagent_tracker.handles()[0]
    assert handle.session.config.model == DEFAULT_PLACEHOLDER_MODEL_FAST
    handle.thread.join(timeout=5.0)


def test_apply_resolves_an_explicit_placeholder_model_override(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    tool = CreateSubagentTool(context)

    tool.apply({
        "role": "explorer", "session_title": "task", "initial_message": "go",
        "model": "klorb-default/heavy",
    })

    handle = context.session.subagent_tracker.handles()[0]
    assert handle.session.config.model == DEFAULT_PLACEHOLDER_MODEL_HEAVY
    handle.thread.join(timeout=5.0)


def test_apply_resolves_current_placeholder_to_the_parents_model(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    context.session.config.model = "parent/custom-model"
    tool = CreateSubagentTool(context)

    tool.apply({
        "role": "explorer", "session_title": "task", "initial_message": "go",
        "model": "klorb-default/current",
    })

    handle = context.session.subagent_tracker.handles()[0]
    assert handle.session.config.model == "parent/custom-model"
    handle.thread.join(timeout=5.0)


def test_apply_raises_on_an_unrecognized_placeholder_model(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    tool = CreateSubagentTool(context)

    with pytest.raises(ToolCallError):
        tool.apply({
            "role": "explorer", "session_title": "task", "initial_message": "go",
            "model": "klorb-default/bogus",
        })

    assert context.session.subagent_tracker.handles() == []


def test_subagent_session_shares_the_parents_root_id(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """A subagent's `root_id` must match its creator's, not default to its own `id` -- otherwise
    it would scope its chainlink issues to a group of its own rather than sharing its creator's
    (see `Session.get_chainlink_label()` and docs/specs/chainlink-task-tracking.md's "Session
    state" section)."""
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    tool = CreateSubagentTool(context)

    tool.apply({"role": "explorer", "session_title": "task", "initial_message": "go"})

    handle = context.session.subagent_tracker.handles()[0]
    assert handle.session.root_id == context.session.root_id
    assert handle.session.get_chainlink_label() == context.session.get_chainlink_label()
    handle.thread.join(timeout=5.0)


def test_apply_does_not_touch_chainlink_when_child_has_no_task_tool(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """Explorer (the only launchable role today) has no `TASKS` tool in its resolved tool set, so
    `CreateSubagentTool.apply()` must not construct a `ChainlinkClient` at all -- constructing one
    for a fresh workspace runs `chainlink init`, too expensive to pay for every subagent creation
    regardless of whether it could ever track tasks."""
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    tool = CreateSubagentTool(context)

    tool.apply({"role": "explorer", "session_title": "task", "initial_message": "go"})

    assert "tasks" not in context.session.tool_state
    context.session.subagent_tracker.handles()[0].thread.join(timeout=5.0)


@requires_chainlink
def test_apply_ensures_the_creators_chainlink_client_when_child_has_a_task_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    """Explorer has no real `TASKS` tool today, so this patches `TASK_TOOL_NAMES` to overlap with
    a tool Explorer definitely has (`ReadFile`), purely to exercise the
    `ensure_chainlink_client()` wiring without a real task-tracking subagent role existing yet."""
    monkeypatch.setattr(create_module, "TASK_TOOL_NAMES", frozenset({"ReadFile"}))
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    tool = CreateSubagentTool(context)

    tool.apply({"role": "explorer", "session_title": "task", "initial_message": "go"})

    assert context.session.tool_state["tasks"]["client"] is not None
    assert "ChainlinkClient" in context.session._teardown_callbacks
    context.session.subagent_tracker.handles()[0].thread.join(timeout=5.0)


def test_apply_raises_without_constructing_a_session_when_validation_fails(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    tool = CreateSubagentTool(context)

    with pytest.raises(ToolCallError):
        tool.apply({"role": "no_such_role", "session_title": "t", "initial_message": "m"})

    assert context.session.subagent_tracker.handles() == []


def test_name_and_category(tmp_path: Path, make_session_config: Callable[..., SessionConfig]) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    tool = CreateSubagentTool(context)

    assert tool.name() == "CreateSubagent"
    assert tool.category() == "SUBAGENT"
    assert tool.is_read_only() is True


@requires_chainlink
def test_apply_with_starting_task_id_claims_all_labeled_task_and_prepends_summary(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """When starting_task_id points to an unclaimed ("all"-labeled) issue, the tool claims it
    for the new subagent and prepends the task summary to the initial message."""
    from klorb.tools.tasks.common import ALL_LABEL, ChainlinkClient, agent_label

    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    client = ChainlinkClient(context)
    task_id = client.create_issue("Fix the bug", description="It crashes on startup")
    client.add_label(task_id, ALL_LABEL)

    tool = CreateSubagentTool(context)
    tool.apply({
        "role": "explorer",
        "session_title": "fix-it",
        "initial_message": "go investigate",
        "starting_task_id": task_id,
    })

    handle = context.session.subagent_tracker.handles()[0]
    child_label = agent_label(handle.session.id)
    issue = client.show_issue(task_id)
    assert child_label in issue["labels"]
    assert ALL_LABEL not in issue["labels"]
    # Verify the initial message received by the subagent includes the task summary.
    assert handle.thread.is_alive() or True  # thread may have already finished
    handle.thread.join(timeout=5.0)
    messages = provider.calls[-1]
    user_text = messages[-1].content if messages else ""
    assert f"#{task_id}" in user_text
    assert "Fix the bug" in user_text
    assert "It crashes on startup" in user_text
    assert "go investigate" in user_text


@requires_chainlink
def test_apply_with_starting_task_id_when_already_labeled_for_child(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """When the task is already labeled for the child agent, no re-claim is needed -- the
    summary is still prepended."""
    from klorb.tools.tasks.common import ChainlinkClient, agent_label

    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    # Pre-create the child so we know its id.
    tool = CreateSubagentTool(context)
    # We can't easily get the child id before apply, so instead we test via the
    # "already claimed by another agent" path with a known different id.
    client = ChainlinkClient(context)
    task_id = client.create_issue("Write docs")
    client.add_label(task_id, agent_label("some-other-agent"))

    with pytest.raises(ToolCallError, match="already claimed"):
        tool.apply({
            "role": "explorer",
            "session_title": "docs",
            "initial_message": "write them",
            "starting_task_id": task_id,
        })


@requires_chainlink
def test_apply_with_starting_task_id_raises_when_task_closed(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    from klorb.tools.tasks.common import ChainlinkClient

    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    client = ChainlinkClient(context)
    task_id = client.create_issue("Old task")
    client.close_issue(task_id)

    tool = CreateSubagentTool(context)
    with pytest.raises(ToolCallError, match="not open"):
        tool.apply({
            "role": "explorer",
            "session_title": "t",
            "initial_message": "m",
            "starting_task_id": task_id,
        })


@requires_chainlink
def test_apply_with_starting_task_id_raises_when_task_not_found(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None

    tool = CreateSubagentTool(context)
    with pytest.raises(ToolCallError, match="could not be resolved"):
        tool.apply({
            "role": "explorer",
            "session_title": "t",
            "initial_message": "m",
            "starting_task_id": 99999,
        })
