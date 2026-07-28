# © Copyright 2026 Aaron Kimball
"""Tests for klorb.workspace.session_store."""

from datetime import datetime
from pathlib import Path

import pytest

from klorb.lockfile import create_lockfile
from klorb.message import Message, MessageRole
from klorb.schema_envelope import write_versioned_json
from klorb.session import SessionConfig
from klorb.workspace import Workspace
from klorb.workspace import input_history as input_history_module
from klorb.workspace.session_store import (
    MAX_RECENT_SESSIONS,
    SESSION_STATE_SCHEMA_NAME,
    SESSION_STATE_SCHEMA_VERSION,
    SESSIONS_LIST_SCHEMA_NAME,
    SESSIONS_LIST_SCHEMA_VERSION,
    RecentSession,
    find_recent_session,
    read_session_state,
    read_sessions_index,
    session_lock_path,
    session_state_path,
    sessions_list_path,
    touch_recent_session,
    write_session_state,
)


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the shared per-project directory helper at an empty `$KLORB_DATA_DIR` under
    `tmp_path`, so no test in this module reads or writes the developer's own
    `~/.local/share/klorb/projects/`."""
    monkeypatch.setattr(input_history_module, "KLORB_DATA_DIR", tmp_path / "data")


def _workspace(tmp_path: Path) -> Workspace:
    return Workspace(id="abcd-1234", path=tmp_path / "foobar", is_project=True, trusted=True)


def _message(role: MessageRole = "user", content: str = "hello") -> Message:
    return Message(
        content=content, role=role, num_tokens=3, processing_state="complete",
        timestamp=datetime(2026, 7, 12, 0, 0, 0))


class TestSessionState:
    def test_read_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        assert read_session_state(workspace, "sess-1") is None

    def test_write_then_read_round_trips_config_and_messages(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        config = SessionConfig(model="some/model", workspace=workspace)
        messages = [_message("user", "hi there"), _message("assistant", "hello!")]

        write_session_state(workspace, "sess-1", config, messages)
        state = read_session_state(workspace, "sess-1")

        assert state is not None
        assert state.config.model == "some/model"
        assert [m.content for m in state.messages] == ["hi there", "hello!"]

    def test_write_includes_schema_envelope(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        write_session_state(workspace, "sess-1", SessionConfig(), [])

        import json
        raw = json.loads(session_state_path(workspace, "sess-1").read_text(encoding="utf-8"))
        assert raw["schema"] == {"name": SESSION_STATE_SCHEMA_NAME, "version": SESSION_STATE_SCHEMA_VERSION}

    def test_two_subdirs_are_independent(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        write_session_state(workspace, "sess-1", SessionConfig(), [_message(content="first")])
        write_session_state(workspace, "sess-2", SessionConfig(), [_message(content="second")])

        state1 = read_session_state(workspace, "sess-1")
        state2 = read_session_state(workspace, "sess-2")
        assert state1 is not None
        assert state2 is not None
        assert [m.content for m in state1.messages] == ["first"]
        assert [m.content for m in state2.messages] == ["second"]

    def test_read_returns_none_for_wrong_schema_name(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        write_versioned_json(
            session_state_path(workspace, "sess-1"), {"config": {}, "messages": []},
            schema_name="klorb-config", schema_version="1.0.0")
        assert read_session_state(workspace, "sess-1") is None

    def test_read_returns_none_for_invalid_shape(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        write_versioned_json(
            session_state_path(workspace, "sess-1"), {"config": {}, "messages": "not-a-list"},
            schema_name=SESSION_STATE_SCHEMA_NAME, schema_version=SESSION_STATE_SCHEMA_VERSION)
        assert read_session_state(workspace, "sess-1") is None

    def test_round_trips_aliases(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        write_session_state(
            workspace, "sess-1", SessionConfig(), [], session_id="final-id",
            session_name="Fixed auth", aliases=["old-id", "older-id"])

        state = read_session_state(workspace, "sess-1")
        assert state is not None
        assert state.session_id == "final-id"
        assert state.aliases == ["old-id", "older-id"]

    def test_read_returns_empty_aliases_for_old_files(self, tmp_path: Path) -> None:
        """Old session.json files written by an older klorb version lack an `aliases` field."""
        workspace = _workspace(tmp_path)
        write_session_state(workspace, "sess-1", SessionConfig(), [], session_id="old-id")

        state = read_session_state(workspace, "sess-1")
        assert state is not None
        assert state.aliases == []

    def test_round_trips_session_id_name_and_chainlink_task(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        write_session_state(
            workspace, "sess-1", SessionConfig(), [], session_id="2026-07-19-01-50-fix-auth",
            session_name="Fix auth token refresh bug", cur_chainlink_task_id=7)

        state = read_session_state(workspace, "sess-1")
        assert state is not None
        assert state.session_id == "2026-07-19-01-50-fix-auth"
        assert state.session_name == "Fix auth token refresh bug"
        assert state.cur_chainlink_task_id == 7


class TestRecentSessionsIndex:
    def test_read_empty_index_for_missing_file(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        index = read_sessions_index(workspace)
        assert index.recent_sessions == []

    def test_touch_inserts_new_entry_at_front(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        touch_recent_session(workspace, "sess-1", "sess-1", "First session")

        index = read_sessions_index(workspace)
        assert index.recent_sessions == [
            RecentSession(session_id="sess-1", subdir="sess-1", title="First session")]

    def test_touch_moves_existing_entry_to_front(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        touch_recent_session(workspace, "sess-1", "sess-1", "First")
        touch_recent_session(workspace, "sess-2", "sess-2", "Second")
        touch_recent_session(workspace, "sess-1", "sess-1", "First (renamed)")

        index = read_sessions_index(workspace)
        assert [entry.session_id for entry in index.recent_sessions] == ["sess-1", "sess-2"]
        assert index.recent_sessions[0].title == "First (renamed)"

    def test_touch_writes_schema_envelope(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        touch_recent_session(workspace, "sess-1", "sess-1", "First")

        import json
        raw = json.loads(sessions_list_path(workspace).read_text(encoding="utf-8"))
        assert raw["schema"]["name"] == SESSIONS_LIST_SCHEMA_NAME

    def test_touch_persists_aliases(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        touch_recent_session(
            workspace, "final-id", "2026-07-19-01-50-abcd", "Fix auth",
            aliases=["2026-07-19-01-50-abcd"])

        index = read_sessions_index(workspace)
        assert index.recent_sessions[0].aliases == ["2026-07-19-01-50-abcd"]

    def test_touch_re_keys_session_id_without_changing_subdir(self, tmp_path: Path) -> None:
        """A session-naming rename changes `session_id` but not `subdir` -- re-touching under
        the new id must replace the old entry, not duplicate it, and keep the original
        `subdir`."""
        workspace = _workspace(tmp_path)
        touch_recent_session(workspace, "2026-07-19-01-50-abcd", "2026-07-19-01-50-abcd", None)
        touch_recent_session(workspace, "2026-07-19-01-50-fix-auth", "2026-07-19-01-50-abcd", "Fix auth")

        index = read_sessions_index(workspace)
        assert len(index.recent_sessions) == 1
        assert index.recent_sessions[0].session_id == "2026-07-19-01-50-fix-auth"
        assert index.recent_sessions[0].subdir == "2026-07-19-01-50-abcd"

    def test_read_returns_empty_for_wrong_schema_name(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        write_versioned_json(
            sessions_list_path(workspace), {"recent_sessions": []},
            schema_name="klorb-config", schema_version="1.0.0")
        assert read_sessions_index(workspace).recent_sessions == []

    def test_read_returns_empty_for_invalid_shape(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        write_versioned_json(
            sessions_list_path(workspace), {"recent_sessions": "not-a-list"},
            schema_name=SESSIONS_LIST_SCHEMA_NAME, schema_version="1.0.0")
        assert read_sessions_index(workspace).recent_sessions == []


class TestPruning:
    def test_prunes_oldest_entries_past_max_recent_sessions(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        for i in range(MAX_RECENT_SESSIONS + 3):
            session_id = f"sess-{i}"
            write_session_state(workspace, session_id, SessionConfig(), [])
            touch_recent_session(workspace, session_id, session_id, f"Session {i}")

        index = read_sessions_index(workspace)
        assert len(index.recent_sessions) == MAX_RECENT_SESSIONS
        # Most recently touched (highest i) survive; the oldest 3 (sess-0..sess-2) are gone.
        surviving_ids = {entry.session_id for entry in index.recent_sessions}
        assert "sess-0" not in surviving_ids
        assert "sess-2" not in surviving_ids
        assert f"sess-{MAX_RECENT_SESSIONS + 2}" in surviving_ids

    def test_pruning_deletes_the_dropped_session_directory(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        for i in range(MAX_RECENT_SESSIONS + 1):
            session_id = f"sess-{i}"
            write_session_state(workspace, session_id, SessionConfig(), [])
            touch_recent_session(workspace, session_id, session_id, f"Session {i}")

        assert read_session_state(workspace, "sess-0") is None
        assert not session_state_path(workspace, "sess-0").parent.exists()

    def test_locked_entry_survives_past_the_cap(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        for i in range(MAX_RECENT_SESSIONS):
            session_id = f"sess-{i}"
            write_session_state(workspace, session_id, SessionConfig(), [])
            touch_recent_session(workspace, session_id, session_id, f"Session {i}")

        # Lock sess-0 (the entry about to fall off the end) as if a live process still owns it.
        lock = create_lockfile(session_lock_path(workspace, "sess-0"))
        assert lock.try_acquire()
        try:
            write_session_state(workspace, "sess-new", SessionConfig(), [])
            touch_recent_session(workspace, "sess-new", "sess-new", "Newest")

            index = read_sessions_index(workspace)
            assert len(index.recent_sessions) == MAX_RECENT_SESSIONS + 1
            assert "sess-0" in {entry.session_id for entry in index.recent_sessions}
            assert session_state_path(workspace, "sess-0").exists()
        finally:
            lock.release()


class TestFindRecentSession:
    def test_finds_by_current_session_id(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        touch_recent_session(workspace, "current-id", "subdir", "Session")

        index = read_sessions_index(workspace)
        entry = find_recent_session(index, "current-id")
        assert entry is not None
        assert entry.session_id == "current-id"

    def test_finds_by_alias(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        touch_recent_session(
            workspace, "final-id", "subdir", "Session",
            aliases=["pre-rename-id"])

        index = read_sessions_index(workspace)
        entry = find_recent_session(index, "pre-rename-id")
        assert entry is not None
        assert entry.session_id == "final-id"

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        touch_recent_session(workspace, "other-id", "subdir", "Session")

        index = read_sessions_index(workspace)
        assert find_recent_session(index, "no-such-id") is None

    def test_backward_compat_with_old_entries_without_aliases(self, tmp_path: Path) -> None:
        """Old entries written by a previous klorb version have no `aliases` field."""
        workspace = _workspace(tmp_path)
        # Simulate an old entry serialized directly (no aliases field at all)
        import json

        from klorb.schema_envelope import write_versioned_json
        write_versioned_json(
            sessions_list_path(workspace),
            {"recent_sessions": [
                json.loads(RecentSession(
                    session_id="old-id", subdir="old-id", title="Old"
                ).model_dump_json())]},
            schema_name=SESSIONS_LIST_SCHEMA_NAME, schema_version=SESSIONS_LIST_SCHEMA_VERSION)

        index = read_sessions_index(workspace)
        assert find_recent_session(index, "old-id") is not None
        assert find_recent_session(index, "no-such-id") is None
