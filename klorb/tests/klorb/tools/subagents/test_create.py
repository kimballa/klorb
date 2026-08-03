# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.subagents.create.CreateSubagentTool."""

from pathlib import Path

import pytest
from tools.subagents.conftest import _FakeProvider

from klorb.api_provider import ApiProvider
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.exceptions import ToolCallError
from klorb.tools.registry import ToolRegistry
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.subagents.create import CreateSubagentTool
from klorb.workspace import Workspace


def _operator_context(tmp_path: Path, provider: ApiProvider) -> ToolSetupContext:
    process_config = ProcessConfig()
    session_config = SessionConfig(role_name="operator", workspace=Workspace(path=tmp_path))
    tool_registry = ToolRegistry.discover_tools(process_config, session_config)
    session = Session(
        session_config, provider=provider, process_config=process_config, tool_registry=tool_registry)
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
