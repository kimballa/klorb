# © Copyright 2026 Aaron Kimball
"""Builds the `_klorb/chatHistory` ext-method result payload from a session tree's chat room
state."""

from typing import Any

from klorb.agents.chat import CHAT_USER_ID
from klorb.session import Session


def build_chat_history_snapshot(root: Session) -> dict[str, Any]:
    """Every retained message in `root`'s chat channel, oldest first, plus the user's own unread
    tallies.

    Registering `CHAT_USER_ID` on every call is safe: it seeds a fresh high-water mark only the
    first time it's called.
    """
    channel = root.chat_channel
    channel.register_participant(CHAT_USER_ID)
    messages = [
        {
            "seq": message.seq,
            "senderId": message.sender_id,
            "timestamp": message.timestamp.isoformat(),
            "body": message.body,
        }
        for message in channel.history()
    ]
    return {
        "messages": messages,
        "unreadCount": channel.unread_count(CHAT_USER_ID),
        "unreadMentionCount": channel.unread_mention_count(CHAT_USER_ID),
    }
