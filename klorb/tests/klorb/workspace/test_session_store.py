# © Copyright 2026 Aaron Kimball
"""Tests for klorb.workspace.session_store."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from klorb.lockfile import create_lockfile
from klorb.message import Message, MessageFragment, MessageRole
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
    read_session_image,
    read_session_state,
    read_sessions_index,
    session_lock_path,
    session_state_path,
    sessions_dir,
    sessions_list_path,
    touch_recent_session,
    write_session_image,
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

        raw = json.loads(session_state_path(workspace, "sess-1").read_text(encoding="utf-8"))
        assert raw["schema"] == {"name": SESSION_STATE_SCHEMA_NAME,
            "version": SESSION_STATE_SCHEMA_VERSION}

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

    def test_round_trips_last_modified_timestamp(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        timestamp = datetime(2026, 7, 19, 1, 50, 0)
        write_session_state(
            workspace, "sess-1", SessionConfig(), [], last_modified_timestamp=timestamp)

        state = read_session_state(workspace, "sess-1")
        assert state is not None
        assert state.last_modified_timestamp == timestamp

    def test_read_returns_none_last_modified_timestamp_for_old_files(self, tmp_path: Path) -> None:
        """Old session.json files written by an older klorb version lack this field."""
        workspace = _workspace(tmp_path)
        write_session_state(workspace, "sess-1", SessionConfig(), [])

        state = read_session_state(workspace, "sess-1")
        assert state is not None
        assert state.last_modified_timestamp is None

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


class TestSessionImages:
    """Tests for write_session_image/read_session_image and write_session_state's use of
    Message.for_persistence() to keep base64 image bytes out of session.json -- see
    docs/adrs/store-image-fragments-on-disk-not-inline-in-session-json.md."""

    def test_write_then_read_round_trips_bytes(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        data = b"some raw image bytes"

        image_path = write_session_image(workspace, "sess-1", data, "webp")

        assert image_path.startswith("images/")
        assert image_path.endswith(".webp")
        assert read_session_image(workspace, "sess-1", image_path) == data

    def test_two_writes_get_distinct_paths(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        path_a = write_session_image(workspace, "sess-1", b"a", "png")
        path_b = write_session_image(workspace, "sess-1", b"b", "png")
        assert path_a != path_b

    def test_write_session_state_drops_image_url_once_image_path_is_set(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        image_path = write_session_image(workspace, "sess-1", b"raw bytes", "webp")
        message = Message(
            content="look", role="user", num_tokens=1, processing_state="complete",
            timestamp=datetime(2026, 7, 12, 0, 0, 0),
            fragments=[MessageFragment(
                type="image_url", image_url={"url": "data:image/webp;base64,xx"},
                image_path=image_path, mime_type="image/webp")])

        write_session_state(workspace, "sess-1", SessionConfig(), [message])

        raw = json.loads(session_state_path(workspace, "sess-1").read_text(encoding="utf-8"))
        stored_fragment = raw["messages"][0]["fragments"][0]
        assert stored_fragment["image_url"] is None
        assert stored_fragment["image_path"] == image_path

    def test_write_then_read_round_trips_filename_and_original_dimensions(
        self, tmp_path: Path,
    ) -> None:
        """`source_filename`/`original_width`/`original_height` survive a session.json round
        trip (unlike `resized_width`/`resized_height`, which don't) -- so a `_klorb/
        sessionReplay` restore can still caption a restored attachment even though its bytes
        aren't resent (`klorb.server.update_mapping.build_session_replay`)."""
        workspace = _workspace(tmp_path)
        image_path = write_session_image(workspace, "sess-1", b"raw bytes", "webp")
        message = Message(
            content="look", role="user", num_tokens=1, processing_state="complete",
            timestamp=datetime(2026, 7, 12, 0, 0, 0),
            fragments=[MessageFragment(
                type="image_url", image_path=image_path, mime_type="image/webp",
                source_filename="shot.png", original_width=123, original_height=456,
                resized_width=64, resized_height=64)])

        write_session_state(workspace, "sess-1", SessionConfig(), [message])
        state = read_session_state(workspace, "sess-1")

        assert state is not None
        restored_fragment = state.messages[0].fragments[0]  # type: ignore[index]
        assert restored_fragment.source_filename == "shot.png"
        assert restored_fragment.original_width == 123
        assert restored_fragment.original_height == 456
        assert restored_fragment.resized_width is None
        assert restored_fragment.resized_height is None


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

    def test_touch_persists_last_modified_timestamp(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        timestamp = datetime(2026, 7, 19, 1, 50, 0)
        touch_recent_session(
            workspace, "sess-1", "sess-1", "First", last_modified_timestamp=timestamp)

        index = read_sessions_index(workspace)
        assert index.recent_sessions[0].last_modified_timestamp == timestamp

    def test_touch_defaults_last_modified_timestamp_to_none(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        touch_recent_session(workspace, "sess-1", "sess-1", "First")

        index = read_sessions_index(workspace)
        assert index.recent_sessions[0].last_modified_timestamp is None

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

    def test_traversal_subdir_is_dropped_without_touching_filesystem(self, tmp_path: Path) -> None:
        """A hand-edited `sessions.json` with a `..`-escaping `subdir` must not reach
        `shutil.rmtree` -- the outside-of-`sessions/` target it names is left untouched, and the
        bogus entry is simply dropped from the index."""
        workspace = _workspace(tmp_path)
        victim_dir = tmp_path / "victim"
        victim_dir.mkdir(parents=True)
        (victim_dir / "keep-me.txt").write_text("do not delete", encoding="utf-8")

        for i in range(MAX_RECENT_SESSIONS):
            session_id = f"sess-{i}"
            write_session_state(workspace, session_id, SessionConfig(), [])
            touch_recent_session(workspace, session_id, session_id, f"Session {i}")

        malicious = "../../../victim"
        write_versioned_json(
            sessions_list_path(workspace),
            {"recent_sessions": [
                json.loads(entry.model_dump_json())
                for entry in read_sessions_index(workspace).recent_sessions] + [
                {"session_id": "evil", "subdir": malicious, "title": "Evil"}]},
            schema_name=SESSIONS_LIST_SCHEMA_NAME, schema_version=SESSIONS_LIST_SCHEMA_VERSION)

        write_session_state(workspace, "sess-new", SessionConfig(), [])
        touch_recent_session(workspace, "sess-new", "sess-new", "Newest")

        assert (victim_dir / "keep-me.txt").exists()
        index = read_sessions_index(workspace)
        assert "evil" not in {entry.session_id for entry in index.recent_sessions}

    def test_symlinked_subdir_component_escaping_sessions_dir_is_dropped(
        self, tmp_path: Path,
    ) -> None:
        """A `subdir` with no `..` in it at all, like `malicious_link/somewhere`, is still
        unsafe if `malicious_link` is a symlink pointing outside `sessions_dir(workspace)` --
        resolving it lands outside the session store just the same, so it must be dropped
        without `shutil.rmtree` ever reaching the real target."""
        workspace = _workspace(tmp_path)
        victim_dir = tmp_path / "victim"
        victim_dir.mkdir(parents=True)
        (victim_dir / "keep-me.txt").write_text("do not delete", encoding="utf-8")

        for i in range(MAX_RECENT_SESSIONS):
            session_id = f"sess-{i}"
            write_session_state(workspace, session_id, SessionConfig(), [])
            touch_recent_session(workspace, session_id, session_id, f"Session {i}")

        (sessions_dir(workspace) / "malicious_link").symlink_to(victim_dir, target_is_directory=True)
        malicious = "malicious_link/somewhere"
        write_versioned_json(
            sessions_list_path(workspace),
            {"recent_sessions": [
                json.loads(entry.model_dump_json())
                for entry in read_sessions_index(workspace).recent_sessions] + [
                {"session_id": "evil", "subdir": malicious, "title": "Evil"}]},
            schema_name=SESSIONS_LIST_SCHEMA_NAME, schema_version=SESSIONS_LIST_SCHEMA_VERSION)

        write_session_state(workspace, "sess-new", SessionConfig(), [])
        touch_recent_session(workspace, "sess-new", "sess-new", "Newest")

        assert (victim_dir / "keep-me.txt").exists()
        index = read_sessions_index(workspace)
        assert "evil" not in {entry.session_id for entry in index.recent_sessions}

    def test_subdir_inside_a_symlinked_sessions_dir_is_still_pruned_normally(
        self, tmp_path: Path,
    ) -> None:
        """If `sessions_dir(workspace)` itself is a symlink pointing somewhere outside its
        natural location under `$KLORB_DATA_DIR`, an ordinary nested `subdir` inside that
        retargeted tree is still safe: the containment check resolves `sessions_dir(workspace)`
        too, so it compares against where the symlink actually points, not its nominal path.
        Confirmed by checking that pruning it actually deletes its (real, retargeted) directory
        -- the unsafe-subdir path would instead leave it on disk and only drop the index entry.
        """
        workspace = _workspace(tmp_path)
        real_sessions_dir = tmp_path / "elsewhere" / "real-sessions"
        real_sessions_dir.mkdir(parents=True)
        nominal_sessions_dir = sessions_dir(workspace)
        nominal_sessions_dir.parent.mkdir(parents=True, exist_ok=True)
        nominal_sessions_dir.symlink_to(real_sessions_dir, target_is_directory=True)

        write_session_state(workspace, "foo/bar", SessionConfig(), [])
        touch_recent_session(workspace, "nested", "foo/bar", "Nested")
        assert (real_sessions_dir / "foo" / "bar" / "session.json").exists()

        for i in range(MAX_RECENT_SESSIONS):
            session_id = f"sess-{i}"
            write_session_state(workspace, session_id, SessionConfig(), [])
            touch_recent_session(workspace, session_id, session_id, f"Session {i}")

        index = read_sessions_index(workspace)
        assert "nested" not in {entry.session_id for entry in index.recent_sessions}
        assert not (real_sessions_dir / "foo" / "bar").exists()

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
