# © Copyright 2026 Aaron Kimball
"""Tests for `klorb.server.chat_updates.build_chat_history_snapshot`."""
from collections.abc import Callable
from unittest.mock import MagicMock

from klorb.agents.chat import CHAT_USER_ID
from klorb.server.chat_updates import build_chat_history_snapshot
from klorb.session import Session, SessionConfig


def test_empty_channel_reports_no_messages_and_no_unread(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    root = Session(make_session_config(role_name="operator"), provider=MagicMock())

    snapshot = build_chat_history_snapshot(root)

    assert snapshot == {"messages": [], "unreadCount": 0, "unreadMentionCount": 0}


def test_posted_messages_report_in_order_with_sender_and_body(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    root = Session(make_session_config(role_name="operator"), provider=MagicMock())
    root.chat_channel.post(root.id, "first", root)
    root.chat_channel.post(CHAT_USER_ID, "second", root)

    snapshot = build_chat_history_snapshot(root)

    assert [m["senderId"] for m in snapshot["messages"]] == [root.id, CHAT_USER_ID]
    assert [m["body"] for m in snapshot["messages"]] == ["first", "second"]
    assert [m["seq"] for m in snapshot["messages"]] == [1, 2]


def test_first_snapshot_seeds_user_hwm_at_now_so_prior_messages_are_not_unread(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    root = Session(make_session_config(role_name="operator"), provider=MagicMock())
    root.chat_channel.post(root.id, "before the user ever opened chat", root)

    snapshot = build_chat_history_snapshot(root)

    assert snapshot["messages"] != []
    assert snapshot["unreadCount"] == 0


def test_message_posted_after_the_first_snapshot_counts_as_unread(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    root = Session(make_session_config(role_name="operator"), provider=MagicMock())
    build_chat_history_snapshot(root)
    root.chat_channel.post(root.id, "posted after the user opened chat", root)

    snapshot = build_chat_history_snapshot(root)

    assert snapshot["unreadCount"] == 1


def test_mention_of_user_increments_unread_mention_count(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    root = Session(make_session_config(role_name="operator"), provider=MagicMock())
    build_chat_history_snapshot(root)
    root.chat_channel.post(root.id, "@user please look at this", root)

    snapshot = build_chat_history_snapshot(root)

    assert snapshot["unreadMentionCount"] == 1
