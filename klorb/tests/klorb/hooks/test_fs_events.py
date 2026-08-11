# © Copyright 2026 Aaron Kimball
"""Tests for klorb.hooks.fs_events.FileSystemWatcher."""

import asyncio
import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from klorb.hooks.config import FileSystemModifiedEventConfig, HookConfig
from klorb.hooks.fs_events import FileSystemWatcher
from klorb.hooks.hook_api import EventInput, FileSystemUpdate

_DEBOUNCE_SECONDS = 0.1


@pytest.fixture
def root(tmp_path: Path) -> Path:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    return workspace_root


def _entry(watch: str) -> FileSystemModifiedEventConfig:
    return FileSystemModifiedEventConfig(watch=watch, action=HookConfig(type="chat", prompt="noticed"))


class _Recorder:
    """Captures every `dispatch` call, signaling an `asyncio.Event` so a test can `await` the
    next background-thread delivery instead of polling or sleeping a fixed duration -- mirrors
    `klorb.tests.klorb.tui.test_workspace_file_index`'s `_changed_event` pattern."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[FileSystemModifiedEventConfig], EventInput]] = []
        self.event = asyncio.Event()
        self._loop = asyncio.get_running_loop()

    def __call__(self, entries: list[FileSystemModifiedEventConfig], event_input: EventInput) -> None:
        self.calls.append((entries, event_input))
        self._loop.call_soon_threadsafe(self.event.set)

    async def wait(self) -> None:
        await asyncio.wait_for(self.event.wait(), timeout=5.0)
        self.event.clear()


@pytest.fixture
async def recorder() -> _Recorder:
    """An async fixture, not a plain one, so `_Recorder.__init__` captures the test's own
    running event loop (`asyncio.get_running_loop()`) -- a plain fixture runs before
    pytest-asyncio has entered the test coroutine's loop context, which would bind the
    recorder to the wrong (or no) loop entirely."""
    return _Recorder()


def _running_watcher(
    root: Path, entries: list[FileSystemModifiedEventConfig], recorder: _Recorder,
) -> Iterator[FileSystemWatcher]:
    watcher = FileSystemWatcher(root, entries, dispatch=recorder, debounce_seconds=_DEBOUNCE_SECONDS)
    watcher.start()
    try:
        yield watcher
    finally:
        watcher.close()


@pytest.fixture
def watching_dir(root: Path, recorder: _Recorder) -> Iterator[FileSystemWatcher]:
    """A `FileSystemWatcher` watching the whole `root` directory."""
    yield from _running_watcher(root, [_entry(".")], recorder)


async def test_creating_a_file_delivers_a_created_update(
    watching_dir: FileSystemWatcher, root: Path, recorder: _Recorder,
) -> None:
    (root / "new.txt").write_text("x", encoding="utf-8")
    await recorder.wait()

    assert len(recorder.calls) == 1
    entries, event_input = recorder.calls[0]
    assert entries == [_entry(".")]
    assert event_input.fs_updates is not None
    # Writing a new file's content raises both a `created` and (from the write itself) a
    # `modified` inotify event within the same debounce window; only `created`'s presence is
    # this test's concern.
    assert FileSystemUpdate(event="created", path=str((root / "new.txt").resolve())) in event_input.fs_updates


async def test_deleting_a_file_delivers_one_deleted_update(root: Path, recorder: _Recorder) -> None:
    (root / "gone.txt").write_text("x", encoding="utf-8")
    watcher = FileSystemWatcher(
        root, [_entry(".")], dispatch=recorder, debounce_seconds=_DEBOUNCE_SECONDS)
    watcher.start()
    try:
        os.remove(root / "gone.txt")
        await recorder.wait()
    finally:
        watcher.close()

    assert len(recorder.calls) == 1
    _, event_input = recorder.calls[0]
    assert event_input.fs_updates == [
        FileSystemUpdate(event="deleted", path=str((root / "gone.txt").resolve())),
    ]


async def test_modifying_a_file_delivers_one_modified_update(
    watching_dir: FileSystemWatcher, root: Path, recorder: _Recorder,
) -> None:
    target = root / "existing.txt"
    target.write_text("x", encoding="utf-8")
    await recorder.wait()  # the create above; drained so it doesn't leak into this assertion
    recorder.calls.clear()

    target.write_text("y", encoding="utf-8")
    await recorder.wait()

    assert len(recorder.calls) == 1
    _, event_input = recorder.calls[0]
    assert event_input.fs_updates == [FileSystemUpdate(event="modified", path=str(target.resolve()))]


async def test_rapid_changes_within_the_debounce_window_collapse_into_one_delivery(
    watching_dir: FileSystemWatcher, root: Path, recorder: _Recorder,
) -> None:
    (root / "a.txt").write_text("x", encoding="utf-8")
    (root / "b.txt").write_text("x", encoding="utf-8")
    (root / "c.txt").write_text("x", encoding="utf-8")
    await recorder.wait()
    # Give any second (incorrect) delivery a chance to arrive before asserting there's only one.
    await asyncio.sleep(_DEBOUNCE_SECONDS * 2)

    assert len(recorder.calls) == 1
    _, event_input = recorder.calls[0]
    assert event_input.fs_updates is not None
    assert {update.path for update in event_input.fs_updates} == {
        str((root / "a.txt").resolve()), str((root / "b.txt").resolve()), str((root / "c.txt").resolve()),
    }


async def test_only_entries_whose_watch_path_matches_the_change_are_delivered(
    root: Path, recorder: _Recorder,
) -> None:
    (root / "watched").mkdir()
    (root / "unwatched").mkdir()
    watched_entry = _entry("watched")
    unwatched_entry = _entry("unwatched")
    watcher = FileSystemWatcher(
        root, [watched_entry, unwatched_entry], dispatch=recorder,
        debounce_seconds=_DEBOUNCE_SECONDS)
    watcher.start()
    try:
        (root / "watched" / "new.txt").write_text("x", encoding="utf-8")
        await recorder.wait()
    finally:
        watcher.close()

    assert len(recorder.calls) == 1
    entries, event_input = recorder.calls[0]
    assert entries == [watched_entry]
    assert event_input.fs_updates is not None
    assert all(
        update.path == str((root / "watched" / "new.txt").resolve()) for update in event_input.fs_updates)


async def test_a_change_matching_no_entry_is_not_delivered(root: Path, recorder: _Recorder) -> None:
    (root / "watched").mkdir()
    (root / "elsewhere").mkdir()
    watcher = FileSystemWatcher(
        root, [_entry("watched")], dispatch=recorder, debounce_seconds=_DEBOUNCE_SECONDS)
    watcher.start()
    try:
        (root / "elsewhere" / "new.txt").write_text("x", encoding="utf-8")
        await asyncio.sleep(_DEBOUNCE_SECONDS * 3)
    finally:
        watcher.close()

    assert recorder.calls == []


async def test_watching_a_single_file_reports_changes_to_it(root: Path, recorder: _Recorder) -> None:
    target = root / "single.txt"
    target.write_text("x", encoding="utf-8")
    watcher = FileSystemWatcher(
        root, [_entry("single.txt")], dispatch=recorder, debounce_seconds=_DEBOUNCE_SECONDS)
    watcher.start()
    try:
        target.write_text("y", encoding="utf-8")
        await recorder.wait()
    finally:
        watcher.close()

    assert len(recorder.calls) == 1
    _, event_input = recorder.calls[0]
    assert event_input.fs_updates == [FileSystemUpdate(event="modified", path=str(target.resolve()))]


async def test_dispatch_failure_does_not_stop_future_changes_from_being_watched(
    root: Path, recorder: _Recorder, caplog: pytest.LogCaptureFixture,
) -> None:
    """A dispatch failure for one debounced batch (a hook handler error, or
    `Session.deliver_event_message` raising `ChainedHookMessageUndeliverableError` while idle)
    must not stop later filesystem changes from still being watched -- see
    docs/adrs/00186 (chained-turn delivery)."""
    call_count = 0

    def _raise_once_then_record(
        entries: list[FileSystemModifiedEventConfig], event_input: EventInput,
    ) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        recorder(entries, event_input)

    watcher = FileSystemWatcher(
        root, [_entry(".")], dispatch=_raise_once_then_record, debounce_seconds=_DEBOUNCE_SECONDS)
    watcher.start()
    try:
        with caplog.at_level(logging.ERROR, logger="klorb.hooks.fs_events"):
            (root / "first.txt").write_text("x", encoding="utf-8")
            await asyncio.sleep(_DEBOUNCE_SECONDS * 3)
            (root / "second.txt").write_text("x", encoding="utf-8")
            await recorder.wait()
    finally:
        watcher.close()

    assert call_count == 2
    assert len(recorder.calls) == 1
    assert "dispatch failed" in caplog.text


def test_start_is_a_no_op_with_no_entries(root: Path, recorder: _Recorder) -> None:
    watcher = FileSystemWatcher(root, [], dispatch=recorder)
    watcher.start()
    try:
        assert watcher._observer is None
    finally:
        watcher.close()


def test_close_is_safe_to_call_more_than_once(root: Path, recorder: _Recorder) -> None:
    watcher = FileSystemWatcher(root, [_entry(".")], dispatch=recorder, debounce_seconds=_DEBOUNCE_SECONDS)
    watcher.start()
    watcher.close()
    watcher.close()


def test_watch_path_outside_workspace_is_skipped(
    root: Path, recorder: _Recorder, caplog: pytest.LogCaptureFixture,
) -> None:
    """A `watch` value that resolves outside the workspace root (e.g. `../../etc`) is skipped
    with a warning -- the watcher never schedules it with the Observer."""
    escape_entry = _entry("../../etc")
    watcher = FileSystemWatcher(
        root, [escape_entry], dispatch=recorder, debounce_seconds=_DEBOUNCE_SECONDS)
    watcher.start()
    try:
        assert watcher._observer is not None
        # The escape entry should have been skipped; no watches scheduled with the Observer.
        assert len(watcher._observer._handlers) == 0
        assert any("outside workspace" in r.message for r in caplog.records)
    finally:
        watcher.close()
