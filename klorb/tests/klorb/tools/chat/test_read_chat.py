# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.chat.read_chat.ReadChatTool."""
from collections.abc import Callable
from unittest.mock import MagicMock

from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.chat.post_chat import PostChatTool
from klorb.tools.chat.read_chat import ReadChatTool
from klorb.tools.setup_context import ToolSetupContext


def _tree(
    make_session_config: Callable[..., SessionConfig], process_config: ProcessConfig | None = None,
) -> tuple[ToolSetupContext, ToolSetupContext]:
    process_config = process_config or ProcessConfig()
    root = Session(make_session_config(), provider=MagicMock(), process_config=process_config)
    child = Session(
        make_session_config(role_name="explorer"), provider=MagicMock(), parent=root)
    root_context = ToolSetupContext(
        process_config=process_config, session_config=root.config, session=root)
    child_context = ToolSetupContext(
        process_config=process_config, session_config=child.config, session=child)
    return root_context, child_context


def test_read_returns_nothing_when_no_new_messages(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    _, child_context = _tree(make_session_config)

    result = ReadChatTool(child_context).apply({})

    assert result["count"] == 0
    assert result["remaining_unread"] == 0
    assert ReadChatTool(child_context).format_response(result) == "No new messages waiting."


def test_read_returns_messages_posted_after_the_reader_joined(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    root_context, child_context = _tree(make_session_config)
    PostChatTool(root_context).apply({"message": "hello room"})

    result = ReadChatTool(child_context).apply({})

    assert result["count"] == 1
    assert result["messages"][0]["body"] == "hello room"
    assert result["remaining_unread"] == 0

    # A second call sees nothing new, since read_and_advance already moved the hwm forward.
    assert ReadChatTool(child_context).apply({})["count"] == 0


def test_read_does_not_see_messages_posted_before_the_reader_was_registered(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    process_config = ProcessConfig()
    root = Session(make_session_config(), provider=MagicMock(), process_config=process_config)
    root_context = ToolSetupContext(
        process_config=process_config, session_config=root.config, session=root)
    PostChatTool(root_context).apply({"message": "before you existed"})

    child = Session(
        make_session_config(role_name="explorer"), provider=MagicMock(), parent=root)
    child_context = ToolSetupContext(
        process_config=process_config, session_config=child.config, session=child)

    assert ReadChatTool(child_context).apply({})["count"] == 0


def test_read_caps_the_batch_at_max_read_per_call_and_reports_remaining_unread(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    process_config = ProcessConfig(chat_max_read_per_call=1)
    root_context, child_context = _tree(make_session_config, process_config)
    PostChatTool(root_context).apply({"message": "one"})
    PostChatTool(root_context).apply({"message": "two"})

    result = ReadChatTool(child_context).apply({})

    assert result["count"] == 1
    assert result["remaining_unread"] == 1


def test_read_requested_limit_cannot_exceed_the_process_cap(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    process_config = ProcessConfig(chat_max_read_per_call=1)
    root_context, child_context = _tree(make_session_config, process_config)
    PostChatTool(root_context).apply({"message": "one"})
    PostChatTool(root_context).apply({"message": "two"})

    result = ReadChatTool(child_context).apply({"limit": 10})

    assert result["count"] == 1
