# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.subagents.wait.WaitForSubagentTool."""
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from tools.subagents.conftest import _FakeProvider

from klorb.agents.policy import compute_root_session_grants, dispatch_direct_message
from klorb.agents.runtime import SubagentHandle, SubagentTurnOutcome
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.session.events import QueuedMessage
from klorb.tools.exceptions import ToolCallError, ToolInterruptError
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.subagents.create import CreateSubagentTool
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


def test_raises_immediately_when_no_subagents_are_outstanding(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _operator_context(tmp_path, _FakeProvider(), make_session_config)
    tool = WaitForSubagentTool(context)

    with pytest.raises(ToolCallError, match="no subagents"):
        tool.apply({})


def test_returns_the_finished_subagents_output(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider(reply_text="the answer is 42")
    context = _operator_context(tmp_path, provider, make_session_config)
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


def test_returns_every_subagent_that_finished_before_the_call(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
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


def test_second_wait_fails_once_the_only_subagent_was_already_delivered(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    CreateSubagentTool(context).apply({
        "role": "explorer", "session_title": "task", "initial_message": "go",
    })
    handle = context.session.subagent_tracker.handles()[0]
    handle.thread.join(timeout=5.0)
    WaitForSubagentTool(context).apply({})

    with pytest.raises(ToolCallError, match="no subagents"):
        WaitForSubagentTool(context).apply({})


def test_cancel_event_raises_tool_interrupt_error_instead_of_blocking_forever(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    # A subagent whose background thread never finishes on its own within the test.
    context.session.subagent_tracker.register(SubagentHandle(
        session=Session(make_session_config(), provider=provider, parent=context.session),
        thread=threading.Thread(target=lambda: threading.Event().wait(30)),
        cancel_event=threading.Event(), role="explorer", title="never finishes"))
    context.session.active_cancel_event = threading.Event()
    context.session.active_cancel_event.set()

    with pytest.raises(ToolInterruptError) as exc_info:
        WaitForSubagentTool(context).apply({})

    assert exc_info.value.category == "signaled"
    assert exc_info.value.response_body == {
        "incomplete": True, "incomplete_reason": "user_cancel"}


def test_timeout_raises_tool_interrupt_error_after_timeout_elapses(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    # A subagent whose background thread never finishes on its own within the test.
    context.session.subagent_tracker.register(SubagentHandle(
        session=Session(make_session_config(), provider=provider, parent=context.session),
        thread=threading.Thread(target=lambda: threading.Event().wait(30)),
        cancel_event=threading.Event(), role="explorer", title="never finishes"))

    with pytest.raises(ToolInterruptError) as exc_info:
        WaitForSubagentTool(context).apply({"timeout": 0.3})

    assert exc_info.value.category == "transient"
    assert exc_info.value.response_body["incomplete"] is True
    assert exc_info.value.response_body["incomplete_reason"] == "timeout"
    assert "0.3 seconds elapsed" in exc_info.value.response_body["details"]


def test_user_message_interrupts_wait(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    # A subagent whose background thread never finishes on its own within the test.
    context.session.subagent_tracker.register(SubagentHandle(
        session=Session(make_session_config(), provider=provider, parent=context.session),
        thread=threading.Thread(target=lambda: threading.Event().wait(30)),
        cancel_event=threading.Event(), role="explorer", title="never finishes"))

    def enqueue_after_delay() -> None:
        import time
        time.sleep(0.3)
        assert context.session is not None
        context.session.enqueue_queued_message(QueuedMessage(message_text="hello"))

    enqueuer = threading.Thread(target=enqueue_after_delay)
    enqueuer.start()

    with pytest.raises(ToolInterruptError) as exc_info:
        WaitForSubagentTool(context).apply({})

    enqueuer.join(timeout=5.0)
    assert exc_info.value.category == "signaled"
    assert exc_info.value.response_body == {
        "incomplete": True, "incomplete_reason": "new_message"}


def test_message_already_queued_before_the_wait_interrupts_immediately(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """A message enqueued before apply() started must interrupt the wait up front rather than
    letting the wait clear its event signal and block on top of it."""
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    context.session.subagent_tracker.register(SubagentHandle(
        session=Session(make_session_config(), provider=provider, parent=context.session),
        thread=threading.Thread(target=lambda: threading.Event().wait(30)),
        cancel_event=threading.Event(), role="explorer", title="never finishes"))
    context.session.enqueue_queued_message(QueuedMessage(message_text="already waiting"))

    with pytest.raises(ToolInterruptError) as exc_info:
        WaitForSubagentTool(context).apply({})

    assert exc_info.value.category == "signaled"
    assert exc_info.value.response_body == {
        "incomplete": True, "incomplete_reason": "new_message"}


def test_timeout_works_even_when_user_msg_event_is_set(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """A user message set before apply() starts is cleared; the timeout still fires."""
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    context.session.subagent_tracker.register(SubagentHandle(
        session=Session(make_session_config(), provider=provider, parent=context.session),
        thread=threading.Thread(target=lambda: threading.Event().wait(30)),
        cancel_event=threading.Event(), role="explorer", title="never finishes"))
    # Simulate a user message being enqueued before the wait starts.
    context.session._user_msg_event.set()

    with pytest.raises(ToolInterruptError) as exc_info:
        WaitForSubagentTool(context).apply({"timeout": 0.3})

    # The event was cleared at the start of apply(); timeout fires.
    assert exc_info.value.category == "transient"
    assert exc_info.value.response_body["incomplete_reason"] == "timeout"


def test_never_returns_a_completion_from_a_subagent_a_human_messaged_directly(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """A subagent a human resumed directly (`dispatch_direct_message` on a dormant handle,
    `parent_interested=False`) must never surface through WaitForSubagent -- the parent never
    asked for that reply."""
    provider = _FakeProvider(reply_text="human-addressed reply")
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    CreateSubagentTool(context).apply({
        "role": "explorer", "session_title": "task", "initial_message": "go",
    })
    handle = context.session.subagent_tracker.handles()[0]
    handle.thread.join(timeout=5.0)
    WaitForSubagentTool(context).apply({})  # deliver the first, parent-dispatched turn

    dispatch_direct_message(context.process_config, handle.session, handle, "poking in directly")
    new_handle = context.session.subagent_tracker.handles()[0]
    new_handle.thread.join(timeout=5.0)

    with pytest.raises(ToolCallError, match="no subagents"):
        WaitForSubagentTool(context).apply({})


def test_a_direct_message_enqueued_into_a_running_parent_dispatched_turn_still_delivers(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """A human steering an already-running, parent-dispatched subagent turn (`dispatch_direct_
    message`'s enqueue branch) doesn't opt the parent out of a reply it already expects --
    `parent_interested` stays `True`, and WaitForSubagent still receives it once the turn actually
    finishes."""
    provider = _FakeProvider()
    context = _operator_context(tmp_path, provider, make_session_config)
    assert context.session is not None
    never_finishes = threading.Event()
    child = Session(make_session_config(role_name="explorer"), provider=provider, parent=context.session)
    handle = SubagentHandle(
        session=child, thread=threading.Thread(target=never_finishes.wait, daemon=True),
        cancel_event=threading.Event(), role="explorer", title="task")
    context.session.subagent_tracker.register(handle)
    handle.thread.start()

    dispatch_direct_message(context.process_config, child, handle, "steering it mid-turn")
    drained = child.drain_queued_messages()
    assert [m.message_text for m in drained] == ["steering it mid-turn"]

    never_finishes.set()
    handle.thread.join(timeout=5.0)
    context.session.subagent_tracker.mark_finished(
        child.id, SubagentTurnOutcome(output="final answer", completed=True))

    result = WaitForSubagentTool(context).apply({})

    assert handle.parent_interested is True
    assert result["completed"][0]["output"] == "final answer"
