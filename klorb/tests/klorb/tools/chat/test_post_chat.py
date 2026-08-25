# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.chat.post_chat.PostChatTool."""
import threading
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from tools.subagents.conftest import _FakeProvider

from klorb.agents.runtime import SubagentHandle, SubagentTurnOutcome
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.chat.post_chat import PostChatTool
from klorb.tools.exceptions import ToolCallError
from klorb.tools.setup_context import ToolSetupContext


def _context(
    make_session_config: Callable[..., SessionConfig], process_config: ProcessConfig | None = None,
) -> ToolSetupContext:
    process_config = process_config or ProcessConfig()
    session = Session(make_session_config(), provider=MagicMock(), process_config=process_config)
    return ToolSetupContext(process_config=process_config, session_config=session.config, session=session)


def test_post_assigns_a_sequence_number(make_session_config: Callable[..., SessionConfig]) -> None:
    context = _context(make_session_config)

    result = PostChatTool(context).apply({"message": "hello room"})

    assert result["seq"] == 1
    assert result["mentions"] == []
    assert result["unresolved_mentions"] == []


def test_post_rejects_an_empty_message(make_session_config: Callable[..., SessionConfig]) -> None:
    context = _context(make_session_config)

    with pytest.raises(ToolCallError) as exc_info:
        PostChatTool(context).apply({"message": ""})
    assert exc_info.value.category == "validation"


def test_post_resolves_a_mention_and_is_visible_via_the_shared_channel(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    context = _context(make_session_config)
    assert context.session is not None
    channel = context.session.chat_channel

    PostChatTool(context).apply({"message": f"@{context.session.id} hi"})

    history = channel.history()
    assert len(history) == 1
    assert history[0].body == f"@{context.session.id} hi"
    assert history[0].mentions == [context.session.id]


def test_format_response_reports_mentions_and_unresolved(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    context = _context(make_session_config)
    tool = PostChatTool(context)

    result = tool.apply({"message": "hi @nobody-here"})
    rendered = tool.format_response(result)

    assert "nobody-here" in rendered
    assert "Posted to chat room" in rendered


def test_post_mentioning_a_dormant_subagent_wakes_it(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    provider = _FakeProvider(reply_text="ack")
    process_config = ProcessConfig()
    root = Session(
        make_session_config(role_name="operator"), provider=provider, process_config=process_config)
    child = Session(make_session_config(role_name="explorer"), provider=provider, parent=root)
    handle = SubagentHandle(
        session=child, thread=threading.Thread(target=lambda: None),
        cancel_event=threading.Event(), role="explorer", title="task",
        outcome=SubagentTurnOutcome(output="earlier output", completed=True))
    root.subagent_tracker.register(handle)
    context = ToolSetupContext(process_config=process_config, session_config=root.config, session=root)

    PostChatTool(context).apply({"message": f"@{child.id} please look"})

    new_handle = root.subagent_tracker.current_handle(child.id)
    assert new_handle is not None
    new_handle.thread.join(timeout=5.0)
    assert new_handle.output == "ack"
