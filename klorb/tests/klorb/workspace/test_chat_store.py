# © Copyright 2026 Aaron Kimball
"""Tests for klorb.workspace.chat_store."""

from datetime import datetime
from pathlib import Path

from klorb.agents.chat import ChannelSnapshot, ChatMessage
from klorb.workspace import Workspace
from klorb.workspace.chat_store import read_chat_state, write_chat_state


def _workspace(tmp_path: Path) -> Workspace:
    return Workspace(path=tmp_path / "proj")


def test_read_chat_state_returns_none_when_no_file_exists(tmp_path: Path) -> None:
    assert read_chat_state(_workspace(tmp_path), "sess-1") is None


def test_write_then_read_round_trips_messages_and_hwm(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    messages = [
        ChatMessage(
            seq=1, sender_id="1", timestamp=datetime(2026, 1, 1, 0, 0, 0), body="hi",
            mentions=[], unresolved_mentions=[]),
        ChatMessage(
            seq=2, sender_id="1.1", timestamp=datetime(2026, 1, 1, 0, 0, 1), body="@1 hey",
            mentions=["1"], unresolved_mentions=[]),
    ]

    snapshot = ChannelSnapshot(messages=messages, hwm={"1": 1, "1.1": 0}, next_seq=2, mention_wake_count=1)
    write_chat_state(workspace, "sess-1", snapshot)
    state = read_chat_state(workspace, "sess-1")

    assert state is not None
    assert [m.body for m in state.messages] == ["hi", "@1 hey"]
    assert state.messages[1].mentions == ["1"]
    assert state.hwm == {"1": 1, "1.1": 0}
    assert state.next_seq == 2
    assert state.mention_wake_count == 1
