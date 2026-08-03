# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.subagents.wait.WaitForSubagentTool."""

import threading
from pathlib import Path

import pytest
from tools.subagents.conftest import _FakeProvider

from klorb.agents.policy import compute_root_session_grants
from klorb.agents.runtime import SubagentHandle
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.exceptions import ToolCallError
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.subagents.create import CreateSubagentTool
from klorb.tools.subagents.wait import WaitForSubagentTool
from klorb.workspace import Workspace


def _operator_context(tmp_path: Path, provider: _FakeProvider) -> ToolSetupContext:
    process_config = ProcessConfig()
    session_config = SessionConfig(role_name="operator", workspace=Workspace(path=tmp_path))
    grants = compute_root_session_grants(process_config, session_config, session_config.role_name)
    session_config.skill_rules = grants.skill_rules
    session = Session(
        session_config, provider=provider, process_config=process_config,
        tool_registry=grants.tool_registry, effective_subagent_roles=grants.effective_subagent_roles)
    return ToolSetupContext(process_config=process_config, session_config=session_config, session=session)


def test_raises_immediately_when_no_subagents_are_outstanding(tmp_path: Path) -> None:
    context = _operator_context(tmp_path, _FakeProvider())
    tool = WaitForSubagentTool(context)

    with pytest.raises(ToolCallError, match="no subagents"):
        tool.apply({})


def test_returns_the_finished_subagents_output(tmp_path: Path) -> None:
    provider = _FakeProvider(reply_text="the answer is 42")
    context = _operator_context(tmp_path, provider)
    assert context.session is not None
    CreateSubagentTool(context).apply({
        "role": "explorer", "session_title": "compute", "initial_message": "go",
    })
    handle = context.session.subagent_tracker.handles()[0]
    handle.thread.join(timeout=5.0)

    result = WaitForSubagentTool(context).apply({})

    assert result["completed"] == [{
        "subagent_id": handle.session.id, "role": "explorer", "title": "compute",
        "output": "the answer is 42",
    }]


def test_returns_every_subagent_that_finished_before_the_call(tmp_path: Path) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider)
    assert context.session is not None
    # Each subagent's thread is joined before the next is created, so completion order is
    # deterministic (creation order) rather than a race between the two background threads.
    provider.reply_text = "first done"
    CreateSubagentTool(context).apply({
        "role": "explorer", "session_title": "first", "initial_message": "go",
    })
    context.session.subagent_tracker.handles()[0].thread.join(timeout=5.0)
    provider.reply_text = "second done"
    CreateSubagentTool(context).apply({
        "role": "explorer", "session_title": "second", "initial_message": "go",
    })
    context.session.subagent_tracker.handles()[1].thread.join(timeout=5.0)

    result = WaitForSubagentTool(context).apply({})

    assert [c["title"] for c in result["completed"]] == ["first", "second"]
    assert [c["output"] for c in result["completed"]] == ["first done", "second done"]


def test_second_wait_fails_once_the_only_subagent_was_already_delivered(tmp_path: Path) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider)
    assert context.session is not None
    CreateSubagentTool(context).apply({
        "role": "explorer", "session_title": "task", "initial_message": "go",
    })
    handle = context.session.subagent_tracker.handles()[0]
    handle.thread.join(timeout=5.0)
    WaitForSubagentTool(context).apply({})

    with pytest.raises(ToolCallError, match="no subagents"):
        WaitForSubagentTool(context).apply({})


def test_cancel_event_returns_an_incomplete_result_instead_of_blocking_forever(tmp_path: Path) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider)
    assert context.session is not None
    # A subagent whose background thread never finishes on its own within the test.
    context.session.subagent_tracker.register(SubagentHandle(
        session=Session(SessionConfig(), provider=provider, parent=context.session),
        thread=threading.Thread(target=lambda: threading.Event().wait(30)),
        cancel_event=threading.Event(), role="explorer", title="never finishes"))
    context.session.active_cancel_event = threading.Event()
    context.session.active_cancel_event.set()

    result = WaitForSubagentTool(context).apply({})

    assert result == {"incomplete": True, "incomplete_reason": "user_cancel"}
