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


def _context(scratchpad_path: str) -> ToolSetupContext:
    session_config = SessionConfig()
    session = Session(session_config, provider=MagicMock(), scratchpad_path=scratchpad_path)
    return ToolSetupContext(
        process_config=ProcessConfig(), session_config=session_config, session=session)


def _write(path: Path, content: str) -> Path:
    scratchpad = path / "SCRATCHPAD.md"
    scratchpad.write_text(content)
    return scratchpad


def test_replaces_a_multiline_block(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nb\nc\nd\n")

    result = EditScratchpadTool(_context(str(scratchpad))).apply({
        "old_text": "b\nc", "new_text": "B\nC",
    })

    assert scratchpad.read_text() == "a\nB\nC\nd\n"
    assert result["new_total_lines"] == 4
    assert result["post_edit_content"] == "2|B\n3|C"


def test_insert_by_folding_original_line_into_new_text(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nb\nc\n")

    EditScratchpadTool(_context(str(scratchpad))).apply({
        "old_text": "b", "new_text": "x\nb",
    })

    assert scratchpad.read_text() == "a\nx\nb\nc\n"


def test_delete_via_empty_new_text(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nb\nc\n")

    result = EditScratchpadTool(_context(str(scratchpad))).apply({
        "old_text": "b", "new_text": "",
    })

    assert scratchpad.read_text() == "a\nc\n"
    assert result["new_total_lines"] == 2


def test_insert_into_empty_scratchpad(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "")

    result = EditScratchpadTool(_context(str(scratchpad))).apply({
        "old_text": "", "new_text": "hello\nworld",
    })

    assert scratchpad.read_text() == "hello\nworld\n"
    assert result["new_total_lines"] == 2


def test_old_text_against_empty_scratchpad_names_the_scratchpad(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "")

    with pytest.raises(ValueError, match=r"old_text_start does not match any location in the 0-line"):
        EditScratchpadTool(_context(str(scratchpad))).apply({
            "old_text_start": "x", "old_text_end": "x", "new_text": "hello",
        })


def test_old_text_not_found_names_scratchpad_reread_hint(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "\n".join(f"L{i}" for i in range(1, 11)) + "\n")

    with pytest.raises(ValueError, match="re-ReadScratchpad your scratchpad"):
        EditScratchpadTool(_context(str(scratchpad))).apply({
            "old_text": "NOPE", "new_text": "FIVE",
        })


def test_ambiguous_match_raises(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nDUP\nb\nDUP\nc\n")

    with pytest.raises(ValueError, match=r"Ambiguous match: old_text matches 2 locations"):
        EditScratchpadTool(_context(str(scratchpad))).apply({
            "old_text": "DUP", "new_text": "REPLACED",
        })


def test_old_text_start_end_form_is_wired_through(tmp_path: Path) -> None:
    """Smoke test that old_text_start/old_text_end reaches EditScratchpad via the shared
    EditFileCore -- the full matrix is exercised against EditFile in test_edit_file.py."""
    scratchpad = _write(tmp_path, "a\nb\nc\nd\n")

    result = EditScratchpadTool(_context(str(scratchpad))).apply({
        "old_text_start": "b", "old_text_end": "c", "new_text": "B\nC",
    })

    assert scratchpad.read_text() == "a\nB\nC\nd\n"
    assert result["replaced_lines"] == 2


def test_requires_active_session() -> None:
    context = ToolSetupContext(process_config=ProcessConfig(), session_config=SessionConfig())

    with pytest.raises(ValueError, match="require an active session"):
        EditScratchpadTool(context).apply({"old_text": "a", "new_text": "b"})


def test_name_and_parameters(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\n")
    tool = EditScratchpadTool(_context(str(scratchpad)))
    parameters = tool.parameters()

    assert tool.name() == "EditScratchpad"
    assert set(parameters["required"]) == {"new_text"}
    assert {"old_text", "old_text_start", "old_text_end"} <= set(parameters["properties"])
    assert "filename" not in parameters["properties"]


def test_summary_reports_a_line_diff(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nb\nc\nd\n")
    tool = EditScratchpadTool(_context(str(scratchpad)))
    args = {"old_text": "b\nc", "new_text": "B\nC\nD"}

    result = tool.apply(args)

    assert tool.summary(args, result) == "Edit scratchpad (+3/-2)"


def test_summary_on_failure_omits_the_diff_count(tmp_path: Path) -> None:
    tool = EditScratchpadTool(_context(str(tmp_path / "SCRATCHPAD.md")))
    args = {"old_text": "a", "new_text": "b"}

    assert tool.summary(args, error="not found") == "Edit scratchpad failed: not found"


def test_detail_view_truncates_long_edited_content_to_eight_lines(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\n")
    tool = EditScratchpadTool(_context(str(scratchpad)))
    new_text = "\n".join(f"line{i}" for i in range(20))
    args = {"old_text": "a", "new_text": new_text}

    result = tool.apply(args)
    detail = json.loads(tool.detail_view(args, result))

    assert detail["result"]["post_edit_content"] == "\n".join(f"{i + 1}|line{i}" for i in range(8)) + "\n..."


def test_fuzzy_whitespace_match(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "  a  \nb\nc\n")

    result = EditScratchpadTool(_context(str(scratchpad))).apply({
        "old_text": "a", "new_text": "X",
    })

    assert scratchpad.read_text() == "X\nb\nc\n"
    assert result["fuzzy_whitespace_match"] is True


def test_exact_match_takes_precedence_over_fuzzy(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nb\nc\n")

    result = EditScratchpadTool(_context(str(scratchpad))).apply({
        "old_text": "a", "new_text": "X",
    })

    assert scratchpad.read_text() == "X\nb\nc\n"
    assert "fuzzy_whitespace_match" not in result


def test_fuzzy_match_falls_through_when_still_ambiguous(tmp_path: Path) -> None:
    """Two lines trim to "a"; fuzzy would match both, so the fallback is not honored and the
    exact-match failure surfaces via the ambiguous-match path instead."""
    scratchpad = _write(tmp_path, "  a  \n a \n")

    with pytest.raises(ValueError, match=r"Ambiguous match: old_text matches 2 locations"):
        EditScratchpadTool(_context(str(scratchpad))).apply({
            "old_text": "a", "new_text": "X",
        })


def test_diff_preview_reflects_the_applied_change(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nb\nc\n")
    tool = EditScratchpadTool(_context(str(scratchpad)))
    args = {"old_text": "b", "new_text": "B"}

    result = tool.apply(args)
    preview = tool.diff_preview(args, result)

    assert preview is not None
    assert preview.label == tool.summary(args, result)
    kinds = [line.kind for hunk in preview.hunks for line in hunk.lines]
    assert kinds == ["context", "del", "add", "context"]
