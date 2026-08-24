# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.subagents.get_messages.GetMessagesTool."""
import threading
from collections.abc import Callable
from pathlib import Path

from tools.subagents.conftest import _FakeProvider

from klorb.agents.messaging import get_agent_message_queue
from klorb.agents.runtime import SubagentHandle
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.subagents.get_messages import GetMessagesTool
from klorb.workspace import Workspace


def _session(
    tmp_path: Path, provider: _FakeProvider, make_session_config: Callable[..., SessionConfig],
    *, parent: Session | None = None, role: str = "explorer",
) -> Session:
    session_config = make_session_config(role_name=role, workspace=Workspace(path=tmp_path))
    return Session(session_config, provider=provider, process_config=ProcessConfig(), parent=parent)


def test_reports_no_messages_when_the_queue_is_empty(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _session(tmp_path, _FakeProvider(), make_session_config)
    context = ToolSetupContext(
        process_config=ProcessConfig(), session_config=session.config, session=session)

    reply = GetMessagesTool(context).format_response(GetMessagesTool(context).apply({}))
    assert reply == "No new messages waiting."


def test_formats_a_single_non_parent_message(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _session(tmp_path, _FakeProvider(), make_session_config)
    context = ToolSetupContext(
        process_config=ProcessConfig(), session_config=session.config, session=session)
    get_agent_message_queue(session).enqueue("1.5", "reviewer", session.id, "the quick brown fox")

    reply = GetMessagesTool(context).format_response(GetMessagesTool(context).apply({}))
    assert "You have 1 unread messages:" in reply
    assert "1. From 1.5:\nthe quick brown fox" in reply
    assert "(parent)" not in reply
    assert "use SendMessage" in reply
    assert "sent back to your parent" not in reply


def test_formats_a_mix_of_parent_and_non_parent_messages_and_clears_the_queue(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    provider = _FakeProvider()
    parent = _session(tmp_path, provider, make_session_config, role="operator")
    child = _session(tmp_path, provider, make_session_config, parent=parent)
    handle = SubagentHandle(
        session=child, thread=threading.Thread(target=lambda: None), cancel_event=threading.Event(),
        role="explorer", title="task", parent_interested=False)
    parent.subagent_tracker.register(handle)
    queue = get_agent_message_queue(child)
    queue.enqueue(parent.id, "operator", child.id, "bla bla bla bla")
    queue.enqueue("1.7", "reviewer", child.id, "more more more more")
    context = ToolSetupContext(
        process_config=ProcessConfig(), session_config=child.config, session=child)

    reply = GetMessagesTool(context).format_response(GetMessagesTool(context).apply({}))
    assert "You have 2 unread messages:" in reply
    assert f"1. From {parent.id} (parent):\nbla bla bla bla" in reply
    assert "2. From 1.7:\nmore more more more" in reply
    assert f"sent back to your parent, {parent.id}" in reply
    assert "use SendMessage" in reply
    reactivated_handle = parent.subagent_tracker.current_handle(child.id)
    assert reactivated_handle is not None
    assert reactivated_handle.parent_interested is True

    # A second call sees nothing new -- the first call cleared the queue.
    second_reply = GetMessagesTool(context).format_response(GetMessagesTool(context).apply({}))
    assert second_reply == "No new messages waiting."
