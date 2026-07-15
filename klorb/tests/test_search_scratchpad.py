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


def _parse_lines(result: dict) -> list[tuple[int, str, bool]]:
    """Parse a flat result["lines"] list of dense-format strings.

    Returns list of (line_number, line_content, is_matched) tuples.
    """
    parsed = []
    for line_str in result["lines"]:
        # Format is " 123|content" or "*123|content"
        marker = line_str[0]
        rest = line_str[1:]
        pipe_idx = rest.index("|")
        parsed.append((int(rest[:pipe_idx]), rest[pipe_idx + 1:], marker == "*"))
    return parsed


def test_finds_case_insensitive_match(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "one\nTWO\nthree\nfour\nfive\n")

    result = SearchScratchpadTool(_context(str(scratchpad))).apply({"queries": ["two"]})

    assert result["match_count"] == 1
    matched_lines = [line for line in _parse_lines(result) if line[2]]
    assert len(matched_lines) == 1
    assert matched_lines[0][1] == "TWO"
    assert matched_lines[0][0] == 2


def test_multiple_queries_combine_via_alternation(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "alpha\nbeta\ngamma\ndelta\n")

    result = SearchScratchpadTool(_context(str(scratchpad))).apply({"queries": ["alpha", "delta"]})

    assert result["match_count"] == 2


def test_context_lines_from_config(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "\n".join(f"line{i}" for i in range(10)) + "\n")

    result = SearchScratchpadTool(_context(str(scratchpad), context_lines=1)).apply(
        {"queries": ["line5"], "outputStyle": "FullContext"})

    assert [line[0] for line in _parse_lines(result)] == [5, 6, 7]


def test_adjacent_matches_merge_into_one_block(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nMATCH\nb\nMATCH\nc\n")

    result = SearchScratchpadTool(_context(str(scratchpad), context_lines=1)).apply(
        {"queries": ["MATCH"], "outputStyle": "FullContext"})

    assert result["match_count"] == 2
    assert [line[0] for line in _parse_lines(result)] == [1, 2, 3, 4, 5]


def test_distant_matches_flatten_with_a_line_number_gap(tmp_path: Path) -> None:
    lines = ["x"] * 20
    lines[0] = "MATCH"
    lines[19] = "MATCH"
    scratchpad = _write(tmp_path, "\n".join(lines) + "\n")

    result = SearchScratchpadTool(_context(str(scratchpad), context_lines=1)).apply(
        {"queries": ["MATCH"], "outputStyle": "FullContext"})

    # Two non-contiguous windows concatenated into one lines array; the gap shows only as a jump
    # in the embedded line numbers, with no separator between them.
    assert [line[0] for line in _parse_lines(result)] == [1, 2, 19, 20]


def test_no_matches_returns_empty_lines(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nb\nc\n")

    result = SearchScratchpadTool(_context(str(scratchpad))).apply({"queries": ["nope"]})

    assert result["match_count"] == 0
    assert result["lines"] == []


def test_query_with_regex_metacharacters_is_treated_literally(tmp_path: Path) -> None:
    """queries are literal substrings, never regular expressions: "(" and "a.b" must not be
    interpreted as regex syntax, either raising a compile error or matching unintended text."""
    scratchpad = _write(tmp_path, "a(b\naXb\n")

    result = SearchScratchpadTool(_context(str(scratchpad))).apply({"queries": ["a(b"]})

    assert result["match_count"] == 1
    matched_lines = [line for line in _parse_lines(result) if line[2]]
    assert matched_lines[0][1] == "a(b"


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


def test_detail_view_caps_lines_to_60(tmp_path: Path) -> None:
    lines: list[str] = []
    for i in range(70):
        lines.append(f"MATCH{i}")
        lines.extend(["filler"] * 5)
    scratchpad = _write(tmp_path, "\n".join(lines) + "\n")
    tool = SearchScratchpadTool(_context(str(scratchpad), context_lines=0))
    result = tool.apply({"queries": ["MATCH"]})

    detail = json.loads(tool.detail_view({"queries": ["MATCH"]}, result))

    assert len(detail["result"]["lines"]) == 60
    assert detail["result"]["lines_omitted"] == 10


# --- outputStyle tests ---


def test_default_output_style_is_matches(tmp_path: Path) -> None:
    """When outputStyle is omitted, the default is 'Matches' (only hit lines, no context)."""
    scratchpad = _write(tmp_path, "a\nb\nMATCH\nc\nd\n")
    result = SearchScratchpadTool(_context(str(scratchpad), context_lines=1)).apply(
        {"queries": ["MATCH"]})
    parsed = _parse_lines(result)
    assert [line[0] for line in parsed] == [3]
    assert [line[2] for line in parsed] == [True]


def test_output_style_matches_returns_only_hit_lines(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nb\nMATCH\nc\nd\n")
    result = SearchScratchpadTool(_context(str(scratchpad), context_lines=1)).apply(
        {"queries": ["MATCH"], "outputStyle": "Matches"})
    parsed = _parse_lines(result)
    assert [line[0] for line in parsed] == [3]
    assert [line[2] for line in parsed] == [True]


def test_output_style_full_context_returns_surrounding_lines(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nb\nMATCH\nc\nd\n")
    result = SearchScratchpadTool(_context(str(scratchpad), context_lines=1)).apply(
        {"queries": ["MATCH"], "outputStyle": "FullContext"})
    parsed = _parse_lines(result)
    assert [line[0] for line in parsed] == [2, 3, 4]
    assert [line[2] for line in parsed] == [False, True, False]


def test_list_files_rejected_for_scratchpad(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nb\n")
    with pytest.raises(ValueError, match="ListFiles.*not supported"):
        SearchScratchpadTool(_context(str(scratchpad))).apply(
            {"queries": ["a"], "outputStyle": "ListFiles"})


def test_invalid_output_style_raises_value_error(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\n")
    with pytest.raises(ValueError, match="outputStyle"):
        SearchScratchpadTool(_context(str(scratchpad))).apply(
            {"queries": ["a"], "outputStyle": "bogus"})


def test_invalid_output_style_error_explains_enum_values(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\n")
    with pytest.raises(ValueError, match="outputStyle") as exc_info:
        SearchScratchpadTool(_context(str(scratchpad))).apply(
            {"queries": ["a"], "outputStyle": "bad"})
    msg = str(exc_info.value)
    assert "Matches" in msg
    assert "FullContext" in msg


def test_output_style_empty_string_defaults_to_matches(tmp_path: Path) -> None:
    scratchpad = _write(tmp_path, "a\nb\nMATCH\nc\nd\n")
    result = SearchScratchpadTool(_context(str(scratchpad), context_lines=1)).apply(
        {"queries": ["MATCH"], "outputStyle": ""})
    parsed = _parse_lines(result)
    assert [line[0] for line in parsed] == [3]
