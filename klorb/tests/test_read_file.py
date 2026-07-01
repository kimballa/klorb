# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.read_file."""

from pathlib import Path

import pytest

from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.read_file import MAX_LINES
from klorb.tools.read_file import ReadFileTool
from klorb.tools.setup_context import ToolSetupContext


def _context(max_lines: int = MAX_LINES) -> ToolSetupContext:
    return ToolSetupContext(
        process_config=ProcessConfig(read_file_max_lines=max_lines), session_config=SessionConfig())


def _write_lines(path: Path, count: int) -> Path:
    file_path = path / "sample.txt"
    file_path.write_text("\n".join(f"line {i}" for i in range(1, count + 1)) + "\n")
    return file_path


def test_reads_whole_short_file_by_default(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 3)

    result = ReadFileTool(_context()).apply({"filename": str(file_path)})

    assert result["start_line"] == 1
    assert result["end_line"] == 3
    assert result["total_lines"] == 3
    assert result["truncated"] is False
    assert result["content"] == "1|line 1\n2|line 2\n3|line 3"


def test_start_line_zero_means_start_at_beginning(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 3)

    result = ReadFileTool(_context()).apply({"filename": str(file_path), "start_line": 0})

    assert result["start_line"] == 1
    assert result["content"].startswith("1|line 1")


def test_explicit_range_returns_requested_lines(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 50)

    result = ReadFileTool(_context()).apply({"filename": str(file_path), "start_line": 5, "end_line": 16})

    assert result["start_line"] == 5
    assert result["end_line"] == 16
    assert result["content"].splitlines() == [f"{i}|line {i}" for i in range(5, 17)]


def test_request_beyond_max_lines_is_capped(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 1000)

    result = ReadFileTool(_context()).apply({"filename": str(file_path), "start_line": 1, "end_line": 500})

    assert result["end_line"] == MAX_LINES
    assert len(result["content"].splitlines()) == MAX_LINES
    assert result["truncated"] is True


def test_request_within_max_lines_returns_exact_count(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 50)

    result = ReadFileTool(_context()).apply({"filename": str(file_path), "start_line": 1, "end_line": 12})

    assert len(result["content"].splitlines()) == 12
    assert result["truncated"] is True  # file has more lines beyond what was returned


def test_negative_start_line_raises() -> None:
    with pytest.raises(ValueError, match="start_line must be >= 0"):
        ReadFileTool(_context()).apply({"filename": "irrelevant.txt", "start_line": -1})


def test_end_line_before_start_line_raises() -> None:
    with pytest.raises(ValueError, match="end_line .* must be >= start_line"):
        ReadFileTool(_context()).apply({"filename": "irrelevant.txt", "start_line": 10, "end_line": 5})


def test_name_and_parameters() -> None:
    tool = ReadFileTool(_context())

    assert tool.name() == "ReadFile"
    assert tool.parameters()["required"] == ["filename"]


def test_custom_max_lines_caps_request(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 50)

    result = ReadFileTool(_context(max_lines=5)).apply(
        {"filename": str(file_path), "start_line": 1, "end_line": 50})

    assert result["end_line"] == 5
    assert len(result["content"].splitlines()) == 5
    assert result["truncated"] is True


def test_custom_max_lines_reflected_in_description() -> None:
    tool = ReadFileTool(_context(max_lines=5))

    assert "up to 5" in tool.description()
