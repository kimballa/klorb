# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.widgets.subagents_panel."""

from typing import Literal

from klorb.tui.widgets.subagents_panel import SubagentRowData, SubagentsPanel


def _row(
    session_id: str = "s1", address: str = "1", title: str = "New session...",
    role: str = "operator", state: Literal["running", "finished"] | None = None,
) -> SubagentRowData:
    return SubagentRowData(session_id=session_id, address=address, title=title, role=role, state=state)


def test_render_row_label_root_row_has_no_running_marker() -> None:
    label = SubagentsPanel._render_row_label(_row(), needs_attention=False)

    assert str(label) == "  1 New session..."


def test_render_row_label_running_subagent_is_marked() -> None:
    row = _row(address="1.1", title="explore the bug", state="running")
    label = SubagentsPanel._render_row_label(row, needs_attention=False)

    assert str(label) == "* 1.1 explore the bug"


def test_render_row_label_finished_subagent_has_no_running_marker() -> None:
    row = _row(address="1.1", title="explore the bug", state="finished")
    label = SubagentsPanel._render_row_label(row, needs_attention=False)

    assert str(label) == "  1.1 explore the bug"


def test_render_row_label_attention_needed_is_marked() -> None:
    label = SubagentsPanel._render_row_label(_row(), needs_attention=True)

    assert str(label) == "! 1 New session..."


def test_render_row_label_attention_wins_over_running() -> None:
    row = _row(address="1.1", state="running")
    label = SubagentsPanel._render_row_label(row, needs_attention=True)

    assert str(label) == "! 1.1 New session..."


def test_render_chat_row_label_no_unread() -> None:
    label = SubagentsPanel._render_chat_row_label("none", blink_on=True)

    assert str(label) == "  💬 Chat Room"


def test_render_chat_row_label_plain_unread_is_a_steady_marker() -> None:
    on = SubagentsPanel._render_chat_row_label("unread", blink_on=True)
    off = SubagentsPanel._render_chat_row_label("unread", blink_on=False)

    assert str(on) == "! 💬 Chat Room"
    assert str(off) == "! 💬 Chat Room"


def test_render_chat_row_label_mention_blinks() -> None:
    on = SubagentsPanel._render_chat_row_label("mention", blink_on=True)
    off = SubagentsPanel._render_chat_row_label("mention", blink_on=False)

    assert str(on) == "! 💬 Chat Room"
    assert str(off) == "  💬 Chat Room"
