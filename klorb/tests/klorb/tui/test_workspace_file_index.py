# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.workspace_file_index."""

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from klorb.tui.workspace_file_index import WorkspaceFileIndex, _scan_workspace_files


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

    assert _scan_workspace_files(root) == ["a.txt", "src/main.py"]


def test_scan_skips_gitignored_files(root: Path) -> None:
    (root / ".gitignore").write_text("*.log\nbuild/\n", encoding="utf-8")
    (root / "keep.py").write_text("x", encoding="utf-8")
    (root / "ignored.log").write_text("x", encoding="utf-8")
    (root / "build").mkdir()
    (root / "build" / "output.txt").write_text("x", encoding="utf-8")

    assert _scan_workspace_files(root) == [".gitignore", "keep.py"]


def test_scan_applies_nested_gitignore_rules(root: Path) -> None:
    (root / "pkg").mkdir()
    (root / "pkg" / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
    (root / "pkg" / "secret.txt").write_text("x", encoding="utf-8")
    (root / "pkg" / "public.txt").write_text("x", encoding="utf-8")

    assert _scan_workspace_files(root) == ["pkg/.gitignore", "pkg/public.txt"]


def test_scan_skips_dot_git_directory(root: Path) -> None:
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("x", encoding="utf-8")
    (root / "readme.md").write_text("x", encoding="utf-8")

    assert _scan_workspace_files(root) == ["readme.md"]


@pytest.fixture
def running_index(root: Path) -> Iterator[WorkspaceFileIndex]:
    """A `WorkspaceFileIndex` scoped to `root`, started for the test and always stopped
    afterward so its background observer thread never leaks into a later test."""
    index = WorkspaceFileIndex(root, on_changed=lambda: None)
    index.start()
    try:
        yield index
    finally:
        index.stop()


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
        index.stop()


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
        index.stop()


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
        index.stop()


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
        index.stop()


def test_stop_is_safe_to_call_more_than_once(root: Path) -> None:
    index = WorkspaceFileIndex(root, on_changed=lambda: None)
    index.start()
    index.stop()
    index.stop()
