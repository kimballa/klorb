# © Copyright 2026 Aaron Kimball
"""Tests for klorb.logging_config."""

import io
import logging
import os
from pathlib import Path

from textual.logging import TextualHandler

from klorb import logging_config


def _make_log(logs_dir: Path, name: str, *, size: int = 0, mtime: float | None = None) -> Path:
    """Create a `*.log` file of `size` bytes under `logs_dir`, optionally stamped with `mtime`."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / name
    path.write_bytes(b"x" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_session_log_path_builds_path_under_session_logs_dir() -> None:
    log_path = logging_config.session_log_path("2026-06-30-10-00-happy-otter")

    assert log_path == logging_config.SESSION_LOGS_DIR / "2026-06-30-10-00-happy-otter.log"


def test_configure_logging_creates_session_log_file_when_given_a_path() -> None:
    log_path = logging_config.session_log_path("some-session-id")
    logging_config.configure_logging(repl_mode=True, log_path=log_path)

    assert log_path.exists()
    assert any(isinstance(handler, logging.FileHandler) for handler in logging.getLogger().handlers)


def test_configure_logging_skips_session_log_file_when_no_path_given() -> None:
    logging_config.configure_logging(repl_mode=False, log_path=None)

    assert not any(isinstance(handler, logging.FileHandler) for handler in logging.getLogger().handlers)


def test_configure_logging_attaches_textual_handler_in_repl_mode() -> None:
    logging_config.configure_logging(repl_mode=True, log_path=None)

    handlers = logging.getLogger().handlers
    assert any(isinstance(handler, TextualHandler) for handler in handlers)


def test_configure_logging_attaches_stream_handler_outside_repl_mode() -> None:
    logging_config.configure_logging(repl_mode=False, log_path=None)

    handlers = logging.getLogger().handlers
    assert not any(isinstance(handler, TextualHandler) for handler in handlers)
    assert any(type(handler) is logging.StreamHandler for handler in handlers)


def test_configure_logging_writes_log_records_to_file() -> None:
    log_path = logging_config.session_log_path("another-session-id")
    logging_config.configure_logging(repl_mode=True, log_path=log_path)

    logging.getLogger("klorb.test_logging_config").info("hello from a test")

    assert "hello from a test" in log_path.read_text(encoding="utf-8")


def test_prune_session_logs_no_op_on_empty_dir(tmp_path: Path) -> None:
    logs = tmp_path / "session-logs"
    logs.mkdir()

    logging_config.prune_session_logs(logs, keep_path=logs / "new.log", max_files=3, max_bytes=10**9)

    assert list(logs.glob("*.log")) == []


def test_prune_session_logs_enforces_file_count_cap_reserving_a_slot(tmp_path: Path) -> None:
    logs = tmp_path / "session-logs"
    for i in range(5):
        _make_log(logs, f"log{i}.log", mtime=1000 + i)

    # max_files=3 leaves room for the incoming file, so only the 2 newest existing survive.
    logging_config.prune_session_logs(
        logs, keep_path=logs / "new.log", max_files=3, max_bytes=10**9)

    assert sorted(p.name for p in logs.glob("*.log")) == ["log3.log", "log4.log"]


def test_prune_session_logs_enforces_byte_cap(tmp_path: Path) -> None:
    logs = tmp_path / "session-logs"
    _make_log(logs, "a.log", size=100, mtime=1000)
    _make_log(logs, "b.log", size=100, mtime=1001)
    _make_log(logs, "c.log", size=100, mtime=1002)

    # cap of 250 bytes fits the two newest (200 bytes); the oldest is pruned.
    logging_config.prune_session_logs(
        logs, keep_path=logs / "new.log", max_files=100, max_bytes=250)

    assert sorted(p.name for p in logs.glob("*.log")) == ["b.log", "c.log"]


def test_prune_session_logs_keeps_newest_even_when_over_byte_cap(tmp_path: Path) -> None:
    logs = tmp_path / "session-logs"
    _make_log(logs, "big.log", size=500, mtime=1002)
    _make_log(logs, "mid.log", size=100, mtime=1001)
    _make_log(logs, "old.log", size=100, mtime=1000)

    # The newest file alone busts the 250-byte cap, but the floor keeps it; the rest are pruned.
    logging_config.prune_session_logs(
        logs, keep_path=logs / "new.log", max_files=100, max_bytes=250)

    assert sorted(p.name for p in logs.glob("*.log")) == ["big.log"]


def test_prune_session_logs_never_deletes_keep_path(tmp_path: Path) -> None:
    logs = tmp_path / "session-logs"
    keep = _make_log(logs, "keep.log", size=1000, mtime=2000)
    _make_log(logs, "other.log", size=1000, mtime=1000)

    logging_config.prune_session_logs(logs, keep_path=keep, max_files=1, max_bytes=1)

    assert keep.exists()
    assert not (logs / "other.log").exists()


def test_configure_logging_prunes_old_logs_when_opening_new(tmp_path: Path) -> None:
    logs = tmp_path / "session-logs"
    for i in range(3):
        _make_log(logs, f"old{i}.log", mtime=1000 + i)
    new_log = logs / "new.log"

    logging_config.configure_logging(
        repl_mode=True, log_path=new_log, max_log_files=2, max_log_bytes=10**9)

    # max_log_files=2 reserves a slot for new.log, so only the newest existing log survives.
    assert sorted(p.name for p in logs.glob("*.log")) == ["new.log", "old2.log"]


def test_crash_log_path_includes_workspace_basename_and_tmpdir() -> None:
    path = logging_config.crash_log_path(Path("/home/user/some-project"))

    assert path.parent == Path(logging_config.tempfile.gettempdir())
    assert path.name.startswith("klorb-crash-some-project-")
    assert path.name.endswith(".log")


def test_crash_log_path_falls_back_to_workspace_when_basename_empty() -> None:
    path = logging_config.crash_log_path(Path("/"))

    assert path.name.startswith("klorb-crash-workspace-")


def test_crash_log_tee_writes_to_both_stream_and_file(tmp_path: Path) -> None:
    log_path = tmp_path / "crash.log"
    stream = io.StringIO()
    tee = logging_config.CrashLogTee(stream, log_path)

    tee.write("boom\n")
    tee.flush()

    assert stream.getvalue() == "boom\n"
    assert log_path.read_text(encoding="utf-8") == "boom\n"
    assert tee.opened_log_path() == log_path


def test_crash_log_tee_does_not_create_file_until_first_write(tmp_path: Path) -> None:
    log_path = tmp_path / "crash.log"
    tee = logging_config.CrashLogTee(io.StringIO(), log_path)

    assert tee.opened_log_path() is None
    assert not log_path.exists()


def test_crash_log_tee_falls_back_to_stream_when_file_cannot_be_opened(tmp_path: Path) -> None:
    unwritable_dir = tmp_path / "missing-parent"
    log_path = unwritable_dir / "crash.log"
    stream = io.StringIO()
    tee = logging_config.CrashLogTee(stream, log_path)

    tee.write("boom\n")

    assert stream.getvalue() == "boom\n"
    assert tee.opened_log_path() is None
