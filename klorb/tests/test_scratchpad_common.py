# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.scratchpad.common."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import klorb.tools.scratchpad.common as common
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.scratchpad.common import SCRATCHPAD_FILENAME, Scratchpad, scratchpad_path
from klorb.tools.setup_context import ToolSetupContext


def test_creates_a_fresh_scratchpad_by_default() -> None:
    scratchpad = Scratchpad(None)

    assert scratchpad.path.name == SCRATCHPAD_FILENAME
    assert scratchpad.path.is_file()
    assert scratchpad.path.read_text() == ""


def test_fresh_scratchpads_are_independent_across_instances() -> None:
    first = Scratchpad(None)
    second = Scratchpad(None)

    assert first.path != second.path
    assert first.path.parent != second.path.parent


def test_reuses_a_given_path(tmp_path: Path) -> None:
    shared = tmp_path / "team-scratchpad.md"
    shared.write_text("existing notes\n")

    scratchpad = Scratchpad(str(shared))

    assert scratchpad.path == shared
    assert scratchpad.path.read_text() == "existing notes\n"


def test_reused_path_is_not_touched_or_modified(tmp_path: Path) -> None:
    """A caller-supplied path is trusted as-is: Scratchpad doesn't touch() or otherwise write
    to it, unlike the fresh-directory case."""
    shared = tmp_path / "team-scratchpad.md"
    shared.write_text("existing notes\n")
    original_mtime = shared.stat().st_mtime_ns

    Scratchpad(str(shared))

    assert shared.stat().st_mtime_ns == original_mtime


def test_cleanup_removes_a_freshly_created_scratchpad_directory() -> None:
    scratchpad = Scratchpad(None)
    scratchpad_dir = scratchpad.path.parent

    scratchpad.cleanup()

    assert not scratchpad_dir.exists()


def test_fresh_scratchpad_registers_an_atexit_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fresh-directory branch registers an atexit hook to remove its directory, so it's
    swept on process exit even when Session.close()/cleanup() never runs (the last active
    session on a normal TUI exit, or a crash/SIGKILL)."""
    registered: list[tuple[object, ...]] = []
    monkeypatch.setattr(common.atexit, "register", lambda *args, **kwargs: registered.append(args))

    scratchpad = Scratchpad(None)

    assert (common.shutil.rmtree, scratchpad.path.parent) in registered


def test_reused_scratchpad_registers_no_atexit_sweep(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller-supplied path is never swept by atexit -- its lifecycle isn't ours."""
    registered: list[tuple[object, ...]] = []
    monkeypatch.setattr(common.atexit, "register", lambda *args, **kwargs: registered.append(args))
    shared = tmp_path / "team-scratchpad.md"
    shared.write_text("existing notes\n")

    Scratchpad(str(shared))

    assert registered == []


def test_cleanup_is_a_noop_for_a_reused_path(tmp_path: Path) -> None:
    """cleanup() must never remove a caller-supplied scratchpad -- that file's lifecycle
    belongs to whatever created it, not to this Scratchpad."""
    shared = tmp_path / "team-scratchpad.md"
    shared.write_text("existing notes\n")
    scratchpad = Scratchpad(str(shared))

    scratchpad.cleanup()

    assert shared.is_file()
    assert shared.read_text() == "existing notes\n"


def test_cleanup_is_idempotent() -> None:
    scratchpad = Scratchpad(None)

    scratchpad.cleanup()
    scratchpad.cleanup()  # must not raise


def test_session_close_cleans_up_a_freshly_created_scratchpad() -> None:
    session = Session(SessionConfig(), provider=MagicMock())
    scratchpad_dir = session.scratchpad.path.parent

    session.close()

    assert not scratchpad_dir.exists()


def test_session_close_does_not_remove_a_reused_scratchpad(tmp_path: Path) -> None:
    shared = tmp_path / "team-scratchpad.md"
    shared.write_text("existing notes\n")
    session = Session(SessionConfig(), provider=MagicMock(), scratchpad_path=str(shared))

    session.close()

    assert shared.is_file()
    assert shared.read_text() == "existing notes\n"


def test_scratchpad_path_helper_returns_the_session_scratchpad(tmp_path: Path) -> None:
    scratchpad_file = tmp_path / "SCRATCHPAD.md"
    scratchpad_file.write_text("")
    session_config = SessionConfig()
    session = Session(session_config, provider=MagicMock(), scratchpad_path=str(scratchpad_file))
    context = ToolSetupContext(
        process_config=ProcessConfig(), session_config=session_config, session=session)

    assert scratchpad_path(context) == scratchpad_file


def test_scratchpad_path_helper_requires_an_active_session() -> None:
    context = ToolSetupContext(process_config=ProcessConfig(), session_config=SessionConfig())

    with pytest.raises(ValueError, match="require an active session"):
        scratchpad_path(context)
