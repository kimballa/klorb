# © Copyright 2026 Aaron Kimball
"""Unit tests for `klorb.diagnostics` — the all-thread stack dump written on a force-exit."""

from pathlib import Path

from klorb.diagnostics import dump_all_thread_stacks, thread_dump_path


def test_thread_dump_path_uses_workspace_basename_and_hang_prefix() -> None:
    path = thread_dump_path(Path("/home/someone/my-project"))
    assert path.name.startswith("klorb-hang-my-project-")
    assert path.suffix == ".log"


def test_dump_all_thread_stacks_writes_readable_dump(tmp_path: Path) -> None:
    path = tmp_path / "dump.log"
    result = dump_all_thread_stacks(path)

    assert result == path
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "live thread" in content
    assert "--- Thread " in content  # the human-readable, name-labelled section
    assert "faulthandler" in content  # the C-level appended section
    # This test's own thread must appear in the dump, with a real Python frame.
    assert "test_dump_all_thread_stacks_writes_readable_dump" in content


def test_dump_all_thread_stacks_returns_none_when_unwritable(tmp_path: Path) -> None:
    # Make the "parent directory" actually a file, so mkdir/open both fail and the best-effort
    # dump reports failure rather than raising out of the doomed-process exit path.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    result = dump_all_thread_stacks(blocker / "child.log")
    assert result is None
