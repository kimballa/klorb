# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.workspace_file_index."""

import asyncio
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from klorb.tui import workspace_file_index
from klorb.tui.workspace_file_index import WorkspaceFileIndex, _scan_workspace_files, _ScanAborted


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A fresh subdirectory of `tmp_path`, isolated from the `klorb-config.json` this test
    tree's autouse `_user_config_present` fixture (`tests/klorb/tui/conftest.py`) writes
    directly into `tmp_path` -- these tests assert exact scan results and shouldn't have to
    account for a file unrelated to what they're seeding."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    return workspace_root


def test_scan_lists_files_relative_to_root_sorted(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("x", encoding="utf-8")
    (root / "a.txt").write_text("x", encoding="utf-8")

    assert _scan_workspace_files(root, lambda: False) == ["a.txt", "src/main.py"]


def test_scan_skips_gitignored_files(root: Path) -> None:
    (root / ".gitignore").write_text("*.log\nbuild/\n", encoding="utf-8")
    (root / "keep.py").write_text("x", encoding="utf-8")
    (root / "ignored.log").write_text("x", encoding="utf-8")
    (root / "build").mkdir()
    (root / "build" / "output.txt").write_text("x", encoding="utf-8")

    assert _scan_workspace_files(root, lambda: False) == [".gitignore", "keep.py"]


def test_scan_applies_nested_gitignore_rules(root: Path) -> None:
    (root / "pkg").mkdir()
    (root / "pkg" / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
    (root / "pkg" / "secret.txt").write_text("x", encoding="utf-8")
    (root / "pkg" / "public.txt").write_text("x", encoding="utf-8")

    assert _scan_workspace_files(root, lambda: False) == ["pkg/.gitignore", "pkg/public.txt"]


def test_scan_skips_dot_git_directory(root: Path) -> None:
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("x", encoding="utf-8")
    (root / "readme.md").write_text("x", encoding="utf-8")

    assert _scan_workspace_files(root, lambda: False) == ["readme.md"]


def test_scan_aborts_immediately_when_should_abort_is_already_true(root: Path) -> None:
    (root / "a.txt").write_text("x", encoding="utf-8")

    with pytest.raises(_ScanAborted):
        _scan_workspace_files(root, lambda: True)


def test_scan_checks_should_abort_on_every_subdirectory_recursion(root: Path) -> None:
    """`_scan_dir` polls `should_abort` at the top of every recursive call, not just once up
    front, so a scan already partway into a deep tree still notices `close()` promptly."""
    (root / "a").mkdir()
    (root / "a" / "b").mkdir()
    (root / "a" / "b" / "deep.txt").write_text("x", encoding="utf-8")
    calls = 0

    def should_abort() -> bool:
        nonlocal calls
        calls += 1
        return calls > 2  # let the root call and one subdirectory recursion through first

    with pytest.raises(_ScanAborted):
        _scan_workspace_files(root, should_abort)

    assert calls > 2


@pytest.fixture
def running_index(root: Path) -> Iterator[WorkspaceFileIndex]:
    """A `WorkspaceFileIndex` scoped to `root`, started for the test and always closed
    afterward so its background observer thread never leaks into a later test."""
    index = WorkspaceFileIndex(root, on_changed=lambda: None)
    index.start()
    try:
        yield index
    finally:
        index.close()


async def _wait_for_change(index: WorkspaceFileIndex, changed: asyncio.Event) -> None:
    await asyncio.wait_for(changed.wait(), timeout=5.0)
    changed.clear()


def _changed_event(index: WorkspaceFileIndex) -> asyncio.Event:
    """Rewire `index`'s `on_changed` callback (installed with a no-op by `running_index`) to
    signal an `asyncio.Event`, so a test can `await` the next background-thread update instead
    of polling or sleeping a fixed duration."""
    event = asyncio.Event()
    loop = asyncio.get_event_loop()

    def _signal() -> None:
        loop.call_soon_threadsafe(event.set)

    index._on_changed = _signal
    return event


def test_start_scans_synchronously_before_returning(root: Path) -> None:
    (root / "a.txt").write_text("x", encoding="utf-8")
    index = WorkspaceFileIndex(root, on_changed=lambda: None)
    try:
        index.start()
        assert index.files == ["a.txt"]
    finally:
        index.close()


async def test_creating_a_file_incrementally_adds_it(running_index: WorkspaceFileIndex, root: Path) -> None:
    assert running_index.files == []
    changed = _changed_event(running_index)

    (root / "new.txt").write_text("x", encoding="utf-8")
    await _wait_for_change(running_index, changed)

    assert running_index.files == ["new.txt"]


async def test_deleting_a_file_incrementally_removes_it(root: Path) -> None:
    (root / "gone.txt").write_text("x", encoding="utf-8")
    index = WorkspaceFileIndex(root, on_changed=lambda: None)
    try:
        index.start()
        assert index.files == ["gone.txt"]
        changed = _changed_event(index)

        os.remove(root / "gone.txt")
        await _wait_for_change(index, changed)

        assert index.files == []
    finally:
        index.close()


async def test_creating_a_gitignored_file_is_not_indexed(root: Path) -> None:
    (root / ".gitignore").write_text("*.log\n", encoding="utf-8")
    index = WorkspaceFileIndex(root, on_changed=lambda: None)
    try:
        index.start()
        assert index.files == [".gitignore"]

        (root / "ignored.log").write_text("x", encoding="utf-8")
        # No `on_changed` signal is expected at all for a gitignored path; give the watcher a
        # moment to (not) act, then assert the index is unchanged.
        await asyncio.sleep(0.6)

        assert index.files == [".gitignore"]
    finally:
        index.close()


async def test_editing_gitignore_forces_a_rescan_that_applies_the_new_rule(root: Path) -> None:
    (root / "a.txt").write_text("x", encoding="utf-8")
    (root / "b.log").write_text("x", encoding="utf-8")
    index = WorkspaceFileIndex(root, on_changed=lambda: None)
    try:
        index.start()
        assert sorted(index.files) == ["a.txt", "b.log"]
        changed = _changed_event(index)

        (root / ".gitignore").write_text("*.log\n", encoding="utf-8")
        await _wait_for_change(index, changed)

        assert sorted(index.files) == [".gitignore", "a.txt"]
    finally:
        index.close()


def test_close_is_safe_to_call_more_than_once(root: Path) -> None:
    index = WorkspaceFileIndex(root, on_changed=lambda: None)
    index.start()
    index.close()
    index.close()


# --- concurrency: events arriving while a rescan is in flight (white-box on _rescan/_flush) ---


def test_queueing_during_a_rescan_does_not_schedule_a_flush_timer(root: Path) -> None:
    index = WorkspaceFileIndex(root, on_changed=lambda: None)
    index._rescan_in_progress = True

    index._queue_created(root / "new.txt")
    index._queue_deleted(root / "gone.txt")
    index._queue_rescan()

    assert index._debounce_timer is None
    assert index._pending_created == {"new.txt"}
    assert index._pending_deleted == {"gone.txt"}
    assert index._needs_rescan is True


def test_flush_is_a_no_op_while_rescan_is_in_progress(root: Path) -> None:
    index = WorkspaceFileIndex(root, on_changed=lambda: pytest.fail("on_changed should not fire"))
    index._rescan_in_progress = True
    index._needs_rescan = True
    index._pending_created = {"new.txt"}

    index._flush()

    assert index._needs_rescan is True
    assert index._pending_created == {"new.txt"}


def test_rescan_completion_immediately_flushes_events_queued_mid_scan(
    monkeypatch: pytest.MonkeyPatch, root: Path,
) -> None:
    """A create event that arrives while a rescan is already running must not be lost: queueing
    during the scan skips the debounce timer (see the two tests above), but the rescan itself
    applies whatever queued up the moment it finishes -- immediately, no debounce delay."""
    (root / "a.txt").write_text("x", encoding="utf-8")
    on_changed_calls = 0

    def _on_changed() -> None:
        nonlocal on_changed_calls
        on_changed_calls += 1

    index = WorkspaceFileIndex(root, on_changed=_on_changed)
    real_scan = workspace_file_index._scan_workspace_files

    def fake_scan(workspace_root: Path, should_abort: Callable[[], bool]) -> list[str]:
        # Simulate a watchdog event arriving on another thread while this scan is still
        # running: `_rescan_in_progress` is already True at this point.
        assert index._rescan_in_progress is True
        index._queue_created(root / "created_during_scan.txt")
        assert index._debounce_timer is None
        return real_scan(workspace_root, should_abort)

    monkeypatch.setattr(workspace_file_index, "_scan_workspace_files", fake_scan)

    index._rescan()

    assert index.files == ["a.txt", "created_during_scan.txt"]
    assert index._debounce_timer is None
    # One notification for the rescan itself, one for the immediate follow-up flush.
    assert on_changed_calls == 2


def test_close_during_rescan_aborts_and_leaves_files_untouched(
    monkeypatch: pytest.MonkeyPatch, root: Path,
) -> None:
    (root / "old.txt").write_text("x", encoding="utf-8")
    index = WorkspaceFileIndex(root, on_changed=lambda: pytest.fail("on_changed should not fire"))
    index._files = ["old.txt"]

    def fake_scan(workspace_root: Path, should_abort: Callable[[], bool]) -> list[str]:
        index.close()
        assert should_abort() is True
        raise _ScanAborted()

    monkeypatch.setattr(workspace_file_index, "_scan_workspace_files", fake_scan)

    index._rescan()

    assert index.files == ["old.txt"]
    assert index._rescan_in_progress is False
    assert index.is_closing is True
