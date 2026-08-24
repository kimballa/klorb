# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.subagents.send_message.SendMessageTool."""
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from tools.subagents.conftest import _FakeProvider

from klorb.agents.messaging import get_agent_message_queue
from klorb.agents.policy import compute_root_session_grants, try_wake_next_queued_agent
from klorb.agents.runtime import SubagentHandle, SubagentTurnOutcome
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig, TurnEventHandlers
from klorb.tools.exceptions import ToolCallError, ToolInterruptError
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.subagents.get_messages import GetMessagesTool
from klorb.tools.subagents.send_message import SendMessageTool
from klorb.tools.subagents.wait import WaitForSubagentTool
from klorb.workspace import Workspace


def _role_context(
    tmp_path: Path, provider: _FakeProvider, make_session_config: Callable[..., SessionConfig],
    role_name: str, process_config: ProcessConfig | None = None,
) -> ToolSetupContext:
    process_config = process_config or ProcessConfig()
    session_config = make_session_config(role_name=role_name, workspace=Workspace(path=tmp_path))
    grants = compute_root_session_grants(process_config, session_config, session_config.role_name)
    session_config.skill_rules = grants.skill_rules
    session = Session(
        session_config, provider=provider, process_config=process_config,
        tool_registry=grants.tool_registry, effective_subagent_roles=grants.effective_subagent_roles)
    return ToolSetupContext(process_config=process_config, session_config=session_config, session=session)


def _register_dormant_child(
    parent: Session, provider: _FakeProvider, make_session_config: Callable[..., SessionConfig],
    *, role: str = "explorer", title: str = "task", output: str = "earlier output",
) -> Session:
    child = Session(make_session_config(role_name=role), provider=provider, parent=parent)
    handle = SubagentHandle(
        session=child, thread=threading.Thread(target=lambda: None),
        cancel_event=threading.Event(), role=role, title=title,
        outcome=SubagentTurnOutcome(output=output, completed=True))
    parent.subagent_tracker.register(handle)
    return child


def _join_handle(parent: Session, child_id: str, timeout: float = 5.0) -> None:
    handle = parent.subagent_tracker.current_handle(child_id)
    assert handle is not None
    handle.thread.join(timeout=timeout)


def _register_running_child(
    parent: Session, provider: _FakeProvider, make_session_config: Callable[..., SessionConfig],
    never_finishes: threading.Event, *, role: str = "explorer", title: str = "task",
    parent_interested: bool = True,
) -> Session:
    child = Session(make_session_config(role_name=role), provider=provider, parent=parent)
    handle = SubagentHandle(
        session=child, thread=threading.Thread(target=never_finishes.wait, daemon=True),
        cancel_event=threading.Event(), role=role, title=title, parent_interested=parent_interested)
    parent.subagent_tracker.register(handle)
    handle.thread.start()
    return child


def test_requires_the_send_messages_capability(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    context = _role_context(tmp_path, _FakeProvider(), make_session_config, "explorer")

    with pytest.raises(ToolCallError, match="may not send messages"):
        SendMessageTool(context).apply({"id": "whatever", "message": "hi"})


def test_raises_for_an_unknown_agent_id(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    context = _role_context(tmp_path, _FakeProvider(), make_session_config, "operator")

    with pytest.raises(ToolCallError, match="No such agent"):
        SendMessageTool(context).apply({"id": "no-such-id", "message": "hi"})


def test_raises_for_a_self_addressed_message(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    context = _role_context(tmp_path, _FakeProvider(), make_session_config, "operator")
    assert context.session is not None

    with pytest.raises(ToolCallError, match="cannot send a message to yourself"):
        SendMessageTool(context).apply({"id": context.session.id, "message": "hi"})


def test_delivers_immediately_to_a_dormant_target(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    provider = _FakeProvider(reply_text="second answer")
    context = _role_context(tmp_path, provider, make_session_config, "operator")
    assert context.session is not None
    child = _register_dormant_child(context.session, provider, make_session_config)

    result = SendMessageTool(context).apply({"id": child.id, "message": "any news?"})
    assert result["status"] == "delivered"

    new_handle = context.session.subagent_tracker.current_handle(child.id)
    assert new_handle is not None
    new_handle.thread.join(timeout=5.0)
    assert new_handle.state == "finished"
    assert new_handle.output == "second answer"
    assert new_handle.parent_interested is True


def test_delivers_immediately_to_an_idle_root_target_with_a_wake_handler(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    provider = _FakeProvider()
    context = _role_context(tmp_path, provider, make_session_config, "operator")
    assert context.session is not None
    root = context.session
    woken = threading.Event()
    root.register_wake_handler(woken.set)
    child = _register_dormant_child(root, provider, make_session_config, role="reviewer")
    child_context = ToolSetupContext(
        process_config=context.process_config, session_config=child.config, session=child)

    result = SendMessageTool(child_context).apply({"id": root.id, "message": "status update"})

    assert result["status"] == "delivered"
    assert woken.is_set()
    queued_texts = root.pending_queued_message_texts
    assert len(queued_texts) == 1
    assert "status update" in queued_texts[0]
    assert not get_agent_message_queue(root).has_pending(root.id)


def test_root_target_with_no_host_falls_back_to_the_queue(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    provider = _FakeProvider()
    context = _role_context(tmp_path, provider, make_session_config, "operator")
    assert context.session is not None
    root = context.session
    child = _register_dormant_child(root, provider, make_session_config, role="reviewer")
    child_context = ToolSetupContext(
        process_config=context.process_config, session_config=child.config, session=child)

    result = SendMessageTool(child_context).apply({"id": root.id, "message": "status update"})

    assert result["status"] == "busy"
    assert get_agent_message_queue(root).has_pending(root.id)
    assert root.pending_queued_message_texts == []


def test_busy_root_target_still_uses_the_agent_message_queue(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    provider = _FakeProvider()
    context = _role_context(tmp_path, provider, make_session_config, "operator")
    assert context.session is not None
    root = context.session
    root.register_wake_handler(lambda: None)
    root._current_turn_handlers = TurnEventHandlers()
    child = _register_dormant_child(root, provider, make_session_config, role="reviewer")
    child_context = ToolSetupContext(
        process_config=context.process_config, session_config=child.config, session=child)

    result = SendMessageTool(child_context).apply({"id": root.id, "message": "status update"})

    assert result["status"] == "busy"
    assert get_agent_message_queue(root).has_pending(root.id)


def test_concurrent_sends_to_an_idle_root_target_do_not_drop_a_message(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """Several SendMessage calls landing on an idle root target in quick succession must each
    land in its queued messages rather than race and clobber one another --
    `Session.enqueue_queued_message`'s append is lock-protected, so concurrent callers fold
    instead of dropping."""
    provider = _FakeProvider()
    context = _role_context(tmp_path, provider, make_session_config, "operator")
    assert context.session is not None
    root = context.session
    root.register_wake_handler(lambda: None)
    senders = [
        _register_dormant_child(root, provider, make_session_config, role="reviewer", title=f"s{i}")
        for i in range(5)
    ]

    def send(sender: Session, i: int) -> None:
        sender_context = ToolSetupContext(
            process_config=context.process_config, session_config=sender.config, session=sender)
        SendMessageTool(sender_context).apply({"id": root.id, "message": f"update {i}"})

    threads = [threading.Thread(target=send, args=(sender, i)) for i, sender in enumerate(senders)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)

    queued_texts = root.pending_queued_message_texts
    assert len(queued_texts) == 5
    for i in range(5):
        assert any(f"update {i}" in text for text in queued_texts)


def test_queues_for_a_running_target_and_notifies_it(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    provider = _FakeProvider()
    context = _role_context(tmp_path, provider, make_session_config, "operator")
    assert context.session is not None
    never_finishes = threading.Event()
    child = _register_running_child(context.session, provider, make_session_config, never_finishes)

    try:
        result = SendMessageTool(context).apply({"id": child.id, "message": "status?"})
        assert result["status"] == "busy"
        assert get_agent_message_queue(context.session).has_pending(child.id)
        assert child._user_msg_event.is_set()
    finally:
        never_finishes.set()
        _join_handle(context.session, child.id)


def test_get_messages_delivers_a_queued_message_from_a_running_targets_parent(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    provider = _FakeProvider()
    context = _role_context(tmp_path, provider, make_session_config, "operator")
    assert context.session is not None
    never_finishes = threading.Event()
    # `parent_interested=False` simulates a subagent a human addressed directly -- the point of
    # this test is that GetMessages re-arms interest once a message from its own parent arrives.
    child = _register_running_child(
        context.session, provider, make_session_config, never_finishes, parent_interested=False)
    try:
        SendMessageTool(context).apply({"id": child.id, "message": "status?"})

        child_context = ToolSetupContext(
            process_config=context.process_config, session_config=child.config, session=child)
        get_messages = GetMessagesTool(child_context)
        reply = get_messages.format_response(get_messages.apply({}))
        assert "1 unread messages" in reply
        assert f"From {context.session.id} (parent)" in reply
        assert "sent back to your parent" in reply

        handle = context.session.subagent_tracker.current_handle(child.id)
        assert handle is not None
        assert handle.parent_interested is True
    finally:
        never_finishes.set()
        _join_handle(context.session, child.id)


def test_concurrency_blocked_idle_target_is_queued_instead_of_raising(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    process_config = ProcessConfig(subagents_max_concurrent_per_parent=1)
    provider = _FakeProvider()
    context = _role_context(tmp_path, provider, make_session_config, "operator", process_config)
    assert context.session is not None
    never_finishes = threading.Event()
    running = _register_running_child(context.session, provider, make_session_config, never_finishes)
    dormant = _register_dormant_child(context.session, provider, make_session_config, title="second")

    try:
        result = SendMessageTool(context).apply({"id": dormant.id, "message": "hi"})
        assert result["status"] == "capacity"
        assert get_agent_message_queue(context.session).has_pending(dormant.id)
        handle = context.session.subagent_tracker.current_handle(dormant.id)
        assert handle is not None
        assert handle.state == "finished"
    finally:
        never_finishes.set()
        _join_handle(context.session, running.id)


def test_fifo_wakes_the_oldest_queued_dormant_agent_first(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """When capacity frees up, whichever dormant agent was messaged first is woken first. Both
    finish almost immediately against the fake provider (freeing the sole slot again), so `newer`
    is woken too, moments later, by the wake `older`'s own worker triggers as it finishes -- what
    this asserts is the *order* the provider actually saw the two prompts in, not that `newer`
    stays blocked forever."""
    process_config = ProcessConfig(subagents_max_concurrent_per_parent=1)
    provider = _FakeProvider(reply_text="done")
    context = _role_context(tmp_path, provider, make_session_config, "operator", process_config)
    assert context.session is not None
    never_finishes = threading.Event()
    busy = _register_running_child(context.session, provider, make_session_config, never_finishes)
    older = _register_dormant_child(context.session, provider, make_session_config, title="older")
    newer = _register_dormant_child(context.session, provider, make_session_config, title="newer")

    SendMessageTool(context).apply({"id": older.id, "message": "first"})
    SendMessageTool(context).apply({"id": newer.id, "message": "second"})
    tracker = context.session.subagent_tracker
    older_before = tracker.current_handle(older.id)
    newer_before = tracker.current_handle(newer.id)
    assert older_before is not None
    assert older_before.state == "finished"
    assert newer_before is not None
    assert newer_before.state == "finished"

    # Free the one slot `busy` was holding, then let the scheduler run, exactly as the worker
    # loop would after `busy`'s own turn actually finished.
    never_finishes.set()
    _join_handle(context.session, busy.id)
    context.session.subagent_tracker.mark_finished(
        busy.id, SubagentTurnOutcome(output="busy done", completed=True))
    try_wake_next_queued_agent(process_config, context.session)

    deadline = time.monotonic() + 5.0
    while len(provider.calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert len(provider.calls) == 2
    prompt_texts = [
        "\n".join(m.content for m in call if m.role == "user") for call in provider.calls]
    first_index = next(i for i, text in enumerate(prompt_texts) if "first" in text)
    second_index = next(i for i, text in enumerate(prompt_texts) if "second" in text)
    assert first_index < second_index

    older_handle = tracker.current_handle(older.id)
    newer_handle = tracker.current_handle(newer.id)
    assert older_handle is not None
    older_handle.thread.join(timeout=5.0)
    assert older_handle.output == "done"
    assert newer_handle is not None
    newer_handle.thread.join(timeout=5.0)
    assert newer_handle.output == "done"


def test_wait_for_subagent_is_interrupted_by_a_pending_agent_message(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    provider = _FakeProvider()
    context = _role_context(tmp_path, provider, make_session_config, "operator")
    assert context.session is not None
    never_finishes = threading.Event()
    child = _register_running_child(context.session, provider, make_session_config, never_finishes)
    try:
        get_agent_message_queue(context.session).enqueue(
            "1.9", "reviewer", context.session.id, "wake up")
        context.session.notify_new_message()

        with pytest.raises(ToolInterruptError) as exc_info:
            WaitForSubagentTool(context).apply({})
        assert exc_info.value.response_body["incomplete_reason"] == "new_message"
    finally:
        never_finishes.set()
        _join_handle(context.session, child.id)


def test_queue_full_is_rejected(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    process_config = ProcessConfig(messaging_max_queue_size=1)
    provider = _FakeProvider()
    context = _role_context(tmp_path, provider, make_session_config, "operator", process_config)
    assert context.session is not None
    never_finishes = threading.Event()
    child = _register_running_child(context.session, provider, make_session_config, never_finishes)
    try:
        SendMessageTool(context).apply({"id": child.id, "message": "first"})
        with pytest.raises(ToolCallError) as exc_info:
            SendMessageTool(context).apply({"id": child.id, "message": "second"})
        assert exc_info.value.category == "transient"
    finally:
        never_finishes.set()
        _join_handle(context.session, child.id)
