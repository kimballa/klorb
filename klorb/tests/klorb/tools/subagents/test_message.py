# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.subagents.message.MessageSubagentTool."""
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from tools.subagents.conftest import _FakeProvider

from klorb.agents.policy import compute_root_session_grants
from klorb.agents.runtime import SubagentHandle
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.exceptions import ToolCallError
from klorb.tools.registry import ToolRegistry
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.subagents.create import CreateSubagentTool
from klorb.tools.subagents.message import MessageSubagentTool
from klorb.tools.subagents.wait import WaitForSubagentTool
from klorb.workspace import Workspace


def _operator_context(
    tmp_path: Path, provider: _FakeProvider, make_session_config: Callable[..., SessionConfig]
) -> ToolSetupContext:
    process_config = ProcessConfig()
    session_config = make_session_config(role_name="operator", workspace=Workspace(path=tmp_path))
    grants = compute_root_session_grants(process_config, session_config, session_config.role_name)
    session_config.skill_rules = grants.skill_rules
    session = Session(
        session_config, provider=provider, process_config=process_config,
        tool_registry=grants.tool_registry, effective_subagent_roles=grants.effective_subagent_roles)
    return ToolSetupContext(process_config=process_config, session_config=session_config, session=session)


def test_raises_for_an_unknown_subagent_id(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _operator_context(tmp_path, _FakeProvider(), make_session_config)

    with pytest.raises(ToolCallError, match="No such subagent"):
        MessageSubagentTool(context).apply({"id": "no-such-id", "message": "hi"})


def test_raises_while_the_subagent_is_still_running(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    # A handle deliberately left "running" (its thread blocks on an Event this test controls
    # directly), rather than racing a real CreateSubagent-spawned thread's completion. The
    # event is set (and the thread joined) before the test ends so it doesn't outlive it.
    never_finishes = threading.Event()
    child = Session(make_session_config(role_name="explorer"), provider=provider, parent=context.session)
    handle = SubagentHandle(
        session=child, thread=threading.Thread(target=never_finishes.wait, daemon=True),
        cancel_event=threading.Event(), role="explorer", title="task")
    context.session.subagent_tracker.register(handle)
    handle.thread.start()

    try:
        with pytest.raises(ToolCallError, match="not done its turn yet"):
            MessageSubagentTool(context).apply({"id": handle.session.id, "message": "hi"})
    finally:
        never_finishes.set()
        handle.thread.join(timeout=5.0)


def test_raises_transient_when_resuming_would_exceed_the_concurrent_limit(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider()
    process_config = ProcessConfig(subagents_max_concurrent_per_parent=1)
    session_config = make_session_config(role_name="operator", workspace=Workspace(path=tmp_path))
    tool_registry = ToolRegistry.discover_tools(process_config, session_config)
    session = Session(
        session_config, provider=provider, process_config=process_config, tool_registry=tool_registry)
    context = ToolSetupContext(process_config=process_config, session_config=session_config, session=session)
    # A handle deliberately left "running" (its thread blocks on an Event this test controls
    # directly), occupying the one concurrent slot -- see test_raises_while_the_subagent_is_
    # still_running for why this is built directly rather than via a real CreateSubagent call.
    # The event is set (and the thread joined) before the test ends so it doesn't outlive it.
    never_finishes = threading.Event()
    running_child = Session(make_session_config(role_name="explorer"), provider=provider, parent=session)
    running_handle = SubagentHandle(
        session=running_child, thread=threading.Thread(target=never_finishes.wait, daemon=True),
        cancel_event=threading.Event(), role="explorer", title="first")
    session.subagent_tracker.register(running_handle)
    running_handle.thread.start()
    # A second, already-finished (dormant) subagent this call tries to resume. Its thread was
    # never started -- state="finished" is set directly -- so there's nothing to join.
    dormant_child = Session(make_session_config(role_name="explorer"), provider=provider, parent=session)
    dormant_handle = SubagentHandle(
        session=dormant_child, thread=threading.Thread(target=lambda: None),
        cancel_event=threading.Event(), role="explorer", title="second", state="finished",
        output="done")
    session.subagent_tracker.register(dormant_handle)

    try:
        with pytest.raises(ToolCallError, match="Call WaitForSubagent") as exc_info:
            MessageSubagentTool(context).apply({"id": dormant_child.id, "message": "hi"})
        assert exc_info.value.category == "transient"
    finally:
        never_finishes.set()
        running_handle.thread.join(timeout=5.0)


def test_resumes_a_finished_subagent_and_its_new_output_is_deliverable(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider(reply_text="first answer")
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    CreateSubagentTool(context).apply({
        "role": "explorer", "session_title": "task", "initial_message": "go",
    })
    handle = context.session.subagent_tracker.handles()[0]
    handle.thread.join(timeout=5.0)
    WaitForSubagentTool(context).apply({})  # deliver the first turn's output

    provider.reply_text = "second answer"
    result = MessageSubagentTool(context).apply({"id": handle.session.id, "message": "and then?"})
    assert result["subagent_id"] == handle.session.id
    handle.thread.join(timeout=5.0)

    followup = WaitForSubagentTool(context).apply({})
    assert followup["completed"][0]["output"] == "second answer"


def test_always_produces_a_parent_interested_handle_even_resuming_a_directly_messaged_one(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """MessageSubagent is only ever called by the parent agent itself -- unlike a human's direct
    message (`dispatch_direct_message`), its resume must always mark the parent as expecting a
    reply, even when the subagent it's resuming was last messaged directly by a human (i.e. its
    current handle has `parent_interested=False`)."""
    provider = _FakeProvider(reply_text="human-addressed reply")
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    dormant_child = Session(
        make_session_config(role_name="explorer"), provider=provider, parent=context.session)
    handle = SubagentHandle(
        session=dormant_child, thread=threading.Thread(target=lambda: None),
        cancel_event=threading.Event(), role="explorer", title="task", state="finished",
        output="earlier, human-addressed output", parent_interested=False)
    context.session.subagent_tracker.register(handle)

    provider.reply_text = "parent-addressed reply"
    MessageSubagentTool(context).apply({"id": dormant_child.id, "message": "any news?"})

    new_handle = context.session.subagent_tracker.handles()[0]
    new_handle.thread.join(timeout=5.0)
    assert new_handle.parent_interested is True
    followup = WaitForSubagentTool(context).apply({})
    assert followup["completed"][0]["output"] == "parent-addressed reply"
