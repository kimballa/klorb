# © Copyright 2026 Aaron Kimball
"""Tests for `klorb.server.subagent_updates.build_subagent_tree_snapshot`."""

import threading
from unittest.mock import MagicMock

from klorb.agents.runtime import SUBAGENT_ABORTED_MARKER, SubagentHandle
from klorb.server.subagent_updates import build_subagent_tree_snapshot
from klorb.session import Session, SessionConfig


def _add_subagent(root: Session, role: str = "explorer", title: str = "find the bug") -> SubagentHandle:
    child = Session(
        SessionConfig(role_name=role), provider=MagicMock(), parent=root, session_name=title)
    handle = SubagentHandle(
        session=child, thread=threading.Thread(target=lambda: None), cancel_event=threading.Event(),
        role=role, title=title)
    root.subagent_tracker.register(handle)
    return handle


def test_root_only_tree_reports_one_node_with_the_acp_id_override() -> None:
    root = Session(SessionConfig(role_name="operator"), provider=MagicMock())

    snapshot = build_subagent_tree_snapshot(root, acp_session_id="acp-front-door")

    assert snapshot["nodes"] == [{
        "id": "acp-front-door",
        "parentId": None,
        "address": "1",
        "title": None,
        "role": "operator",
        "state": None,
        "aborted": False,
        "model": root.config.model,
        "thinkingEnabled": root.config.thinking_enabled,
        "thinkingEffort": root.config.thinking_effort,
        "usedTokens": 0,
        "maxTokens": root.max_context_window(),
        "outputTokens": 0,
    }]


def test_subagent_node_reports_its_parent_by_the_overridden_root_id() -> None:
    root = Session(SessionConfig(role_name="operator"), provider=MagicMock())
    handle = _add_subagent(root)

    snapshot = build_subagent_tree_snapshot(root, acp_session_id="acp-front-door")

    child_node = snapshot["nodes"][1]
    assert child_node["id"] == handle.session.id
    assert child_node["parentId"] == "acp-front-door"
    assert child_node["address"] == "1.1"
    assert child_node["state"] == "running"


def test_grandchild_subagent_reports_its_direct_parent_not_the_root() -> None:
    root = Session(SessionConfig(role_name="operator"), provider=MagicMock())
    handle = _add_subagent(root, title="find the bug")
    grandchild = _add_subagent(handle.session, title="dig deeper")

    snapshot = build_subagent_tree_snapshot(root, acp_session_id="acp-front-door")

    grandchild_node = next(
        node for node in snapshot["nodes"] if node["id"] == grandchild.session.id)
    assert grandchild_node["parentId"] == handle.session.id
    assert grandchild_node["address"] == "1.1.1"


def test_finished_handle_with_abort_marker_reports_aborted() -> None:
    root = Session(SessionConfig(role_name="operator"), provider=MagicMock())
    handle = _add_subagent(root)
    handle.state = "finished"
    handle.output = f"partial\n\n{SUBAGENT_ABORTED_MARKER}"

    snapshot = build_subagent_tree_snapshot(root, acp_session_id="acp-front-door")

    child_node = snapshot["nodes"][1]
    assert child_node["state"] == "finished"
    assert child_node["aborted"] is True


def test_finished_handle_without_abort_marker_reports_not_aborted() -> None:
    root = Session(SessionConfig(role_name="operator"), provider=MagicMock())
    handle = _add_subagent(root)
    handle.state = "finished"
    handle.output = "all done"

    snapshot = build_subagent_tree_snapshot(root, acp_session_id="acp-front-door")

    child_node = snapshot["nodes"][1]
    assert child_node["aborted"] is False
