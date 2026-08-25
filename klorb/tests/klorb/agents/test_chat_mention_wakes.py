# © Copyright 2026 Aaron Kimball
"""Tests for klorb.agents.policy.notify_chat_mention."""
import threading
from collections.abc import Callable
from pathlib import Path

from tools.subagents.conftest import _FakeProvider

from klorb.agents.chat import Channel
from klorb.agents.policy import notify_chat_mention
from klorb.agents.runtime import SubagentHandle, SubagentTurnOutcome
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig


def _root(
    tmp_path: Path, provider: _FakeProvider, make_session_config: Callable[..., SessionConfig],
    process_config: ProcessConfig | None = None,
) -> Session:
    return Session(
        make_session_config(role_name="operator"), provider=provider,
        process_config=process_config or ProcessConfig())


def _register_dormant_child(
    parent: Session, provider: _FakeProvider, make_session_config: Callable[..., SessionConfig],
    *, role: str = "explorer", title: str = "task",
) -> Session:
    child = Session(make_session_config(role_name=role), provider=provider, parent=parent)
    handle = SubagentHandle(
        session=child, thread=threading.Thread(target=lambda: None),
        cancel_event=threading.Event(), role=role, title=title,
        outcome=SubagentTurnOutcome(output="earlier output", completed=True))
    parent.subagent_tracker.register(handle)
    return child


def _register_running_child(
    parent: Session, provider: _FakeProvider, make_session_config: Callable[..., SessionConfig],
    never_finishes: threading.Event, *, role: str = "explorer", title: str = "task",
) -> Session:
    child = Session(make_session_config(role_name=role), provider=provider, parent=parent)
    handle = SubagentHandle(
        session=child, thread=threading.Thread(target=never_finishes.wait, daemon=True),
        cancel_event=threading.Event(), role=role, title=title)
    parent.subagent_tracker.register(handle)
    handle.thread.start()
    return child


def test_self_mention_is_a_no_op(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    root = _root(tmp_path, _FakeProvider(), make_session_config)
    channel = Channel()

    notify_chat_mention(ProcessConfig(), channel, root, root.id)

    assert channel.mention_wake_count() == 0


def test_unknown_mention_id_is_a_no_op(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    root = _root(tmp_path, _FakeProvider(), make_session_config)
    channel = Channel()

    notify_chat_mention(ProcessConfig(), channel, root, "no-such-id")

    assert channel.mention_wake_count() == 0


def test_running_target_gets_no_active_wake(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    provider = _FakeProvider()
    process_config = ProcessConfig()
    root = _root(tmp_path, provider, make_session_config, process_config)
    never_finishes = threading.Event()
    child = _register_running_child(root, provider, make_session_config, never_finishes)
    channel = Channel()
    try:
        notify_chat_mention(process_config, channel, root, child.id)
        assert channel.mention_wake_count() == 0
    finally:
        never_finishes.set()
        handle = root.subagent_tracker.current_handle(child.id)
        assert handle is not None
        handle.thread.join(timeout=5.0)


def test_idle_root_target_is_woken_via_deliver_event_message(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    provider = _FakeProvider()
    process_config = ProcessConfig()
    root = _root(tmp_path, provider, make_session_config, process_config)
    root.register_wake_handler(lambda: None)
    child = _register_dormant_child(root, provider, make_session_config, role="reviewer")
    channel = Channel()

    notify_chat_mention(process_config, channel, child, root.id)

    assert channel.mention_wake_count() == 1
    queued_texts = root.pending_queued_message_texts
    assert len(queued_texts) == 1
    assert "@mentioned in the chat room" in queued_texts[0]


def test_dormant_subagent_target_is_resumed(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    provider = _FakeProvider(reply_text="ack")
    process_config = ProcessConfig()
    root = _root(tmp_path, provider, make_session_config, process_config)
    dormant = _register_dormant_child(root, provider, make_session_config)
    channel = Channel()

    notify_chat_mention(process_config, channel, root, dormant.id)

    assert channel.mention_wake_count() == 1
    new_handle = root.subagent_tracker.current_handle(dormant.id)
    assert new_handle is not None
    new_handle.thread.join(timeout=5.0)
    assert new_handle.state == "finished"
    assert new_handle.output == "ack"
    assert new_handle.parent_interested is False


def test_capacity_limited_target_is_skipped_silently(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    provider = _FakeProvider()
    process_config = ProcessConfig(subagents_max_concurrent_per_parent=1)
    root = _root(tmp_path, provider, make_session_config, process_config)
    never_finishes = threading.Event()
    busy = _register_running_child(root, provider, make_session_config, never_finishes)
    dormant = _register_dormant_child(root, provider, make_session_config, title="second")
    channel = Channel()
    try:
        notify_chat_mention(process_config, channel, root, dormant.id)

        assert channel.mention_wake_count() == 0
        handle = root.subagent_tracker.current_handle(dormant.id)
        assert handle is not None
        assert handle.state == "finished"
        assert handle.output == "earlier output"
    finally:
        never_finishes.set()
        busy_handle = root.subagent_tracker.current_handle(busy.id)
        assert busy_handle is not None
        busy_handle.thread.join(timeout=5.0)


def test_wake_cap_degrades_to_passive_only(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    provider = _FakeProvider(reply_text="ack")
    process_config = ProcessConfig(chat_max_mention_wakes=1)
    root = _root(tmp_path, provider, make_session_config, process_config)
    first = _register_dormant_child(root, provider, make_session_config, title="first")
    second = _register_dormant_child(root, provider, make_session_config, title="second")
    channel = Channel()

    notify_chat_mention(process_config, channel, root, first.id)
    assert channel.mention_wake_count() == 1
    first_handle = root.subagent_tracker.current_handle(first.id)
    assert first_handle is not None
    first_handle.thread.join(timeout=5.0)

    notify_chat_mention(process_config, channel, root, second.id)

    # The cap was already reached, so the second mention gets no active wake; the subagent is
    # never resumed.
    assert channel.mention_wake_count() == 1
    second_handle = root.subagent_tracker.current_handle(second.id)
    assert second_handle is not None
    assert second_handle.output == "earlier output"
