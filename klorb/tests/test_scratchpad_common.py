# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.scratchpad.common."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

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
