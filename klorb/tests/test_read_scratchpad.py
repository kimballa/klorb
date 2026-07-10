# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.scratchpad.read."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from klorb.process_config import DEFAULT_READ_FILE_MAX_LINES, ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.scratchpad.read import ReadScratchpadTool
from klorb.tools.setup_context import ToolSetupContext


def _context(scratchpad_path: str, *, max_lines: int = DEFAULT_READ_FILE_MAX_LINES) -> ToolSetupContext:
    session_config = SessionConfig()
    session = Session(session_config, provider=MagicMock(), scratchpad_path=scratchpad_path)
    return ToolSetupContext(
        process_config=ProcessConfig(read_file_max_lines=max_lines),
        session_config=session_config, session=session)


def _write_lines(path: Path, count: int) -> Path:
    scratchpad = path / "SCRATCHPAD.md"
    scratchpad.write_text("\n".join(f"line {i}" for i in range(1, count + 1)) + "\n")
    return scratchpad


def test_reads_whole_short_scratchpad_by_default(tmp_path: Path) -> None:
    scratchpad = _write_lines(tmp_path, 3)

    result = ReadScratchpadTool(_context(str(scratchpad))).apply({})

    assert result["start_line"] == 1
    assert result["end_line"] == 3
    assert result["total_lines"] == 3
    assert result["truncated"] is False
    assert result["content"] == "1|line 1\n2|line 2\n3|line 3"


def test_start_line_zero_means_start_at_beginning(tmp_path: Path) -> None:
    scratchpad = _write_lines(tmp_path, 3)

    result = ReadScratchpadTool(_context(str(scratchpad))).apply({"start_line": 0})

    assert result["start_line"] == 1
    assert result["content"].startswith("1|line 1")


def test_explicit_range_returns_requested_lines(tmp_path: Path) -> None:
    scratchpad = _write_lines(tmp_path, 50)

    result = ReadScratchpadTool(_context(str(scratchpad))).apply({"start_line": 5, "end_line": 16})

    assert result["start_line"] == 5
    assert result["end_line"] == 16
    assert result["content"].splitlines() == [f"{i}|line {i}" for i in range(5, 17)]


def test_request_beyond_max_lines_is_capped(tmp_path: Path) -> None:
    scratchpad = _write_lines(tmp_path, 1000)

    result = ReadScratchpadTool(_context(str(scratchpad))).apply({"start_line": 1, "end_line": 500})

    assert result["end_line"] == DEFAULT_READ_FILE_MAX_LINES
    assert result["truncated"] is True


def test_negative_start_line_raises(tmp_path: Path) -> None:
    scratchpad = _write_lines(tmp_path, 3)

    with pytest.raises(ValueError, match="start_line must be >= 0"):
        ReadScratchpadTool(_context(str(scratchpad))).apply({"start_line": -1})


def test_end_line_before_start_line_raises(tmp_path: Path) -> None:
    scratchpad = _write_lines(tmp_path, 3)

    with pytest.raises(ValueError, match="end_line .* must be >= start_line"):
        ReadScratchpadTool(_context(str(scratchpad))).apply({"start_line": 10, "end_line": 5})


def test_name_and_parameters(tmp_path: Path) -> None:
    scratchpad = _write_lines(tmp_path, 1)
    tool = ReadScratchpadTool(_context(str(scratchpad)))

    assert tool.name() == "ReadScratchpad"
    assert tool.parameters()["required"] == []
    assert "filename" not in tool.parameters()["properties"]


def test_requires_active_session() -> None:
    context = ToolSetupContext(process_config=ProcessConfig(), session_config=SessionConfig())

    with pytest.raises(ValueError, match="require an active session"):
        ReadScratchpadTool(context).apply({})


def test_summary_on_success(tmp_path: Path) -> None:
    scratchpad = _write_lines(tmp_path, 50)
    tool = ReadScratchpadTool(_context(str(scratchpad)))
    result = tool.apply({"start_line": 5, "end_line": 16})

    assert tool.summary({}, result) == "Read scratchpad (lines 5-16 of 50)"


def test_summary_on_failure_includes_the_error() -> None:
    tool = ReadScratchpadTool(_context(str(Path("/tmp") / "SCRATCHPAD.md")))

    assert tool.summary({}, error="not found") == "Read scratchpad failed: not found"


def test_detail_view_truncates_long_content_to_eight_lines(tmp_path: Path) -> None:
    scratchpad = _write_lines(tmp_path, 50)
    tool = ReadScratchpadTool(_context(str(scratchpad)))
    result = tool.apply({})

    detail = json.loads(tool.detail_view({}, result))

    assert detail["result"]["content"] == "\n".join(f"{i}|line {i}" for i in range(1, 9)) + "\n..."
    assert detail["result"]["total_lines"] == 50
