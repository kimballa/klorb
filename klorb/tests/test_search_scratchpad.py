# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.scratchpad.search."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.scratchpad.search import SearchScratchpadTool
from klorb.tools.setup_context import ToolSetupContext


def _context(scratchpad_path: str, *, context_lines: int = 2) -> ToolSetupContext:
    session_config = SessionConfig()
    session = Session(session_config, provider=MagicMock(), scratchpad_path=scratchpad_path)
    return ToolSetupContext(
        process_config=ProcessConfig(scratchpad_context_lines=context_lines),
        session_config=session_config, session=session)


def _write(path: Path, content: str) -> Path:
    scratchpad = path / "SCRATCHPAD.md"
    scratchpad.write_text(content)
    return scratchpad


def test_finds_case_insensitive_match(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "one\nTWO\nthree\nfour\nfive\n")

    result = SearchScratchpadTool(_context(str(scratchpad))).apply({"queries": ["two"]})

    assert result["match_count"] == 1
    matched_lines = [line for block in result["blocks"] for line in block["lines"] if line["matched"]]
    assert len(matched_lines) == 1
    assert matched_lines[0]["line"] == "TWO"
    assert matched_lines[0]["line_number"] == 2


def test_multiple_queries_combine_via_alternation(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "alpha\nbeta\ngamma\ndelta\n")

    result = SearchScratchpadTool(_context(str(scratchpad))).apply({"queries": ["alpha", "delta"]})

    assert result["match_count"] == 2


def test_context_lines_from_config(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "\n".join(f"line{i}" for i in range(10)) + "\n")

    result = SearchScratchpadTool(_context(str(scratchpad), context_lines=1)).apply(
        {"queries": ["line5"]})

    assert len(result["blocks"]) == 1
    block = result["blocks"][0]
    assert block["start_line"] == 5
    assert block["end_line"] == 7


def test_adjacent_matches_merge_into_one_block(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nMATCH\nb\nMATCH\nc\n")

    result = SearchScratchpadTool(_context(str(scratchpad), context_lines=1)).apply(
        {"queries": ["MATCH"]})

    assert result["match_count"] == 2
    assert len(result["blocks"]) == 1
    assert result["blocks"][0]["start_line"] == 1
    assert result["blocks"][0]["end_line"] == 5


def test_distant_matches_produce_separate_blocks(tmp_path: Path) -> None:
    lines = ["x"] * 20
    lines[0] = "MATCH"
    lines[19] = "MATCH"
    scratchpad = _write(tmp_path, "\n".join(lines) + "\n")

    result = SearchScratchpadTool(_context(str(scratchpad), context_lines=1)).apply(
        {"queries": ["MATCH"]})

    assert len(result["blocks"]) == 2


def test_no_matches_returns_empty_blocks(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nb\nc\n")

    result = SearchScratchpadTool(_context(str(scratchpad))).apply({"queries": ["nope"]})

    assert result["match_count"] == 0
    assert result["blocks"] == []


def test_invalid_regex_raises_value_error(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\n")

    with pytest.raises(ValueError, match="Invalid search sequence"):
        SearchScratchpadTool(_context(str(scratchpad))).apply({"queries": ["("]})


def test_empty_queries_raises(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\n")

    with pytest.raises(ValueError, match="non-empty array"):
        SearchScratchpadTool(_context(str(scratchpad))).apply({"queries": []})


def test_non_string_query_raises(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\n")

    with pytest.raises(ValueError, match="must be a string"):
        SearchScratchpadTool(_context(str(scratchpad))).apply({"queries": [1]})


def test_requires_active_session() -> None:
    context = ToolSetupContext(process_config=ProcessConfig(), session_config=SessionConfig())

    with pytest.raises(ValueError, match="require an active session"):
        SearchScratchpadTool(context).apply({"queries": ["x"]})


def test_name_and_parameters(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\n")
    tool = SearchScratchpadTool(_context(str(scratchpad)))

    assert tool.name() == "SearchScratchpad"
    assert tool.parameters()["required"] == ["queries"]


def test_summary_on_success(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "hello\nworld\n")
    tool = SearchScratchpadTool(_context(str(scratchpad)))
    result = tool.apply({"queries": ["hello"]})

    summary = tool.summary({"queries": ["hello"]}, result)
    assert "1 match" in summary


def test_summary_on_failure_includes_the_error() -> None:
    tool = SearchScratchpadTool(_context(str(Path("/tmp") / "SCRATCHPAD.md")))

    assert tool.summary({"queries": ["x"]}, error="boom") == "Search scratchpad: ['x'] failed: boom"


def test_detail_view_caps_blocks_to_20(tmp_path: Path) -> None:
    lines: list[str] = []
    for i in range(30):
        lines.append(f"MATCH{i}")
        lines.extend(["filler"] * 5)
    scratchpad = _write(tmp_path, "\n".join(lines) + "\n")
    tool = SearchScratchpadTool(_context(str(scratchpad), context_lines=0))
    result = tool.apply({"queries": ["MATCH"]})

    detail = json.loads(tool.detail_view({"queries": ["MATCH"]}, result))

    assert len(detail["result"]["blocks"]) == 20
    assert detail["result"]["blocks_omitted"] == 10
