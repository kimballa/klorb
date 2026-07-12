# © Copyright 2026 Aaron Kimball
"""Tests for klorb.workspace.last_session."""

from datetime import datetime
from pathlib import Path

import pytest

from klorb.message import Message, MessageRole
from klorb.schema_envelope import write_versioned_json
from klorb.session import SessionConfig
from klorb.workspace import Workspace
from klorb.workspace import input_history as input_history_module
from klorb.workspace.last_session import (
    LAST_SESSION_SCHEMA_NAME,
    last_session_path,
    read_last_session,
    write_last_session,
)


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the shared per-project directory helper at an empty `$KLORB_DATA_DIR` under
    `tmp_path`, so no test in this module reads or writes the developer's own
    `~/.local/share/klorb/projects/`."""
    monkeypatch.setattr(input_history_module, "KLORB_DATA_DIR", tmp_path / "data")


def _message(role: MessageRole = "user", content: str = "hello") -> Message:
    return Message(
        content=content, role=role, num_tokens=3, processing_state="complete",
        timestamp=datetime(2026, 7, 12, 0, 0, 0))


def test_last_session_path_lives_alongside_history_file(tmp_path: Path) -> None:
    from klorb.workspace.input_history import project_history_dir

    workspace = Workspace(id="abcd-1234", path=tmp_path / "foobar", is_project=True, trusted=True)
    assert last_session_path(workspace) == project_history_dir(workspace) / "last-session.json"


def test_read_last_session_returns_none_for_missing_file(tmp_path: Path) -> None:
    workspace = Workspace(id="abcd-1234", path=tmp_path / "foobar", is_project=True, trusted=True)
    assert read_last_session(workspace) is None


def test_write_then_read_round_trips_config_and_messages(tmp_path: Path) -> None:
    workspace = Workspace(id="abcd-1234", path=tmp_path / "foobar", is_project=True, trusted=True)
    config = SessionConfig(model="some/model", workspace=workspace)
    messages = [_message("user", "hi there"), _message("assistant", "hello!")]

    write_last_session(workspace, config, messages)
    state = read_last_session(workspace)

    assert state is not None
    assert state.config.model == "some/model"
    assert state.config.workspace.path == workspace.path
    assert [m.content for m in state.messages] == ["hi there", "hello!"]
    assert [m.role for m in state.messages] == ["user", "assistant"]


def test_write_last_session_includes_schema_envelope(tmp_path: Path) -> None:
    workspace = Workspace(id="abcd-1234", path=tmp_path / "foobar", is_project=True, trusted=True)
    write_last_session(workspace, SessionConfig(), [])

    import json
    raw = json.loads(last_session_path(workspace).read_text(encoding="utf-8"))
    assert raw["schema"] == {"name": "klorb-session", "version": "1.0.0"}


def test_write_last_session_overwrites_previous_save(tmp_path: Path) -> None:
    workspace = Workspace(id="abcd-1234", path=tmp_path / "foobar", is_project=True, trusted=True)
    write_last_session(workspace, SessionConfig(), [_message(content="first")])
    write_last_session(workspace, SessionConfig(), [_message(content="second")])

    state = read_last_session(workspace)
    assert state is not None
    assert [m.content for m in state.messages] == ["second"]


def test_read_last_session_returns_none_for_wrong_schema_name(tmp_path: Path) -> None:
    workspace = Workspace(id="abcd-1234", path=tmp_path / "foobar", is_project=True, trusted=True)
    write_versioned_json(
        last_session_path(workspace), {"config": {}, "messages": []},
        schema_name="klorb-config", schema_version="1.0.0")

    assert read_last_session(workspace) is None
    assert LAST_SESSION_SCHEMA_NAME == "klorb-session"


def test_tool_call_messages_round_trip(tmp_path: Path) -> None:
    from klorb.message import ToolCallRequest

    workspace = Workspace(id="abcd-1234", path=tmp_path / "foobar", is_project=True, trusted=True)
    tool_use = Message(
        content="", role="tool_use", num_tokens=5, processing_state="complete",
        timestamp=datetime(2026, 7, 12, 0, 0, 0),
        tool_calls=[ToolCallRequest(id="call-1", name="ReadFile", arguments='{"path": "a.txt"}')])
    tool_response = Message(
        content="file contents", role="tool_response", num_tokens=2, processing_state="complete",
        timestamp=datetime(2026, 7, 12, 0, 0, 1), tool_call_id="call-1")

    write_last_session(workspace, SessionConfig(), [tool_use, tool_response])
    state = read_last_session(workspace)

    assert state is not None
    assert state.messages[0].tool_calls is not None
    assert state.messages[0].tool_calls[0].name == "ReadFile"
    assert state.messages[1].tool_call_id == "call-1"
