# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.scratchpad.edit."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.scratchpad.edit import EditScratchpadTool
from klorb.tools.setup_context import ToolSetupContext


def _context(
    scratchpad_path: str, *, process_config: ProcessConfig | None = None,
) -> ToolSetupContext:
    session_config = SessionConfig()
    session = Session(session_config, provider=MagicMock(), scratchpad_path=scratchpad_path)
    return ToolSetupContext(
        process_config=process_config or ProcessConfig(), session_config=session_config,
        session=session)


def _write(path: Path, content: str) -> Path:
    scratchpad = path / "SCRATCHPAD.md"
    scratchpad.write_text(content)
    return scratchpad


def test_replaces_a_line_range(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nb\nc\nd\n")

    result = EditScratchpadTool(_context(str(scratchpad))).apply({
        "start_line": 2, "end_line": 3, "start_text": "b", "end_text": "c", "new_text": "B\nC",
    })

    assert scratchpad.read_text() == "a\nB\nC\nd\n"
    assert result["new_total_lines"] == 4
    assert result["content"] == "2|B\n3|C"


def test_insert_via_single_line_replace(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nb\nc\n")

    EditScratchpadTool(_context(str(scratchpad))).apply({
        "start_line": 2, "end_line": 2, "start_text": "b", "end_text": "b", "new_text": "x\nb",
    })

    assert scratchpad.read_text() == "a\nx\nb\nc\n"


def test_delete_via_empty_new_text(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nb\nc\n")

    result = EditScratchpadTool(_context(str(scratchpad))).apply({
        "start_line": 2, "end_line": 2, "start_text": "b", "end_text": "b", "new_text": "",
    })

    assert scratchpad.read_text() == "a\nc\n"
    assert result["new_total_lines"] == 2


def test_insert_into_empty_scratchpad(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "")

    result = EditScratchpadTool(_context(str(scratchpad))).apply({
        "start_line": 1, "end_line": 0, "start_text": "", "end_text": "", "new_text": "hello\nworld",
    })

    assert scratchpad.read_text() == "hello\nworld\n"
    assert result["new_total_lines"] == 2


def test_empty_scratchpad_error_names_the_scratchpad(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "")

    with pytest.raises(ValueError, match="the scratchpad is empty"):
        EditScratchpadTool(_context(str(scratchpad))).apply({
            "start_line": 1, "end_line": 1, "start_text": "", "end_text": "", "new_text": "x",
        })


def test_drift_within_radius_relocates_single_line_edit(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "p\nq\nr\ns\nt\n")

    result = EditScratchpadTool(_context(str(scratchpad))).apply({
        "start_line": 2, "end_line": 2, "start_text": "r", "end_text": "r", "new_text": "R",
    })

    assert scratchpad.read_text() == "p\nq\nR\ns\nt\n"
    assert result["start_line"] == 3
    assert result["line_hint_matched"] is False


def test_drift_verification_failure_names_scratchpad_reread_hint(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "\n".join(f"L{i}" for i in range(1, 11)) + "\n")

    with pytest.raises(ValueError, match="re-ReadScratchpad your scratchpad"):
        EditScratchpadTool(_context(
            str(scratchpad), process_config=ProcessConfig(edit_file_drift_search_radius=2),
        )).apply({
            "start_line": 1, "end_line": 1, "start_text": "L5", "end_text": "L5", "new_text": "FIVE",
        })


def test_ambiguous_match_raises(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nDUP\nb\nDUP\nc\n")

    with pytest.raises(ValueError, match=r"Ambiguous match.*\[2, 4\]"):
        EditScratchpadTool(_context(str(scratchpad))).apply({
            "start_line": 3, "end_line": 3, "start_text": "DUP", "end_text": "DUP",
            "new_text": "REPLACED",
        })


def test_out_of_range_raises(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nb\nc\n")

    with pytest.raises(ValueError, match="out of range"):
        EditScratchpadTool(_context(str(scratchpad))).apply({
            "start_line": 1, "end_line": 10, "start_text": "a", "end_text": "c", "new_text": "x",
        })


def test_non_integer_line_number_raises(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nb\nc\n")

    with pytest.raises(ValueError, match="start_line must be an integer"):
        EditScratchpadTool(_context(str(scratchpad))).apply({
            "start_line": "1", "end_line": 1, "start_text": "a", "end_text": "a", "new_text": "x",
        })


def test_multiline_start_text_raises(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nb\nc\n")

    with pytest.raises(ValueError, match="start_text must be exactly one line"):
        EditScratchpadTool(_context(str(scratchpad))).apply({
            "start_line": 1, "end_line": 2, "start_text": "a\nb", "end_text": "b", "new_text": "x",
        })


def test_requires_active_session() -> None:
    context = ToolSetupContext(process_config=ProcessConfig(), session_config=SessionConfig())

    with pytest.raises(ValueError, match="require an active session"):
        EditScratchpadTool(context).apply({
            "start_line": 1, "end_line": 1, "start_text": "a", "end_text": "a", "new_text": "b",
        })


def test_name_and_parameters(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\n")
    tool = EditScratchpadTool(_context(str(scratchpad)))
    parameters = tool.parameters()

    assert tool.name() == "EditScratchpad"
    assert set(parameters["required"]) == {
        "start_line", "end_line", "start_text", "end_text", "new_text"}
    assert "filename" not in parameters["properties"]


def test_summary_reports_a_line_diff(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nb\nc\nd\n")
    tool = EditScratchpadTool(_context(str(scratchpad)))
    args = {"start_line": 2, "end_line": 3, "start_text": "b", "end_text": "c", "new_text": "B\nC\nD"}

    result = tool.apply(args)

    assert tool.summary(args, result) == "Edit scratchpad (+3/-2)"


def test_summary_on_failure_includes_the_error_and_diff_count(tmp_path: Path) -> None:
    tool = EditScratchpadTool(_context(str(tmp_path / "SCRATCHPAD.md")))
    args = {"start_line": 1, "end_line": 1, "start_text": "a", "end_text": "a", "new_text": "b"}

    assert tool.summary(args, error="not found") == "Edit scratchpad (+1/-1) failed: not found"


def test_detail_view_truncates_long_edited_content_to_eight_lines(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\n")
    tool = EditScratchpadTool(_context(str(scratchpad)))
    new_text = "\n".join(f"line{i}" for i in range(20))
    args = {"start_line": 1, "end_line": 1, "start_text": "a", "end_text": "a", "new_text": new_text}

    result = tool.apply(args)
    detail = json.loads(tool.detail_view(args, result))

    assert detail["result"]["content"] == "\n".join(f"{i + 1}|line{i}" for i in range(8)) + "\n..."
