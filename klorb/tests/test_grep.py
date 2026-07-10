# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.grep."""

from pathlib import Path

import pytest

from klorb.permissions.directory_access import DirRules
from klorb.permissions.table import PermissionAskRequired
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.grep import GrepTool
from klorb.tools.setup_context import ToolSetupContext
from klorb.workspace import Workspace


def _context(
    workspace_root: Path,
    *,
    max_results: int = 500,
    context_lines: int = 2,
    read_dirs: DirRules | None = None,
) -> ToolSetupContext:
    return ToolSetupContext(
        process_config=ProcessConfig(grep_max_results=max_results, grep_context_lines=context_lines),
        session_config=SessionConfig(
            workspace=Workspace(path=workspace_root),
            read_dirs=read_dirs or DirRules(),
            write_dirs=DirRules(),
        ),
    )


def _make_tree(root: Path) -> None:
    (root / "top.py").write_text("import os\nprint('hello world')\n")
    (root / "sub").mkdir()
    (root / "sub" / "nested.py").write_text("def hello():\n    return 'hello again'\n")
    (root / "sub" / "notes.txt").write_text("hello from notes\n")
    (root / "sub" / "binary.dat").write_bytes(b"\xff\xfe\x00binary hello\x00")


def _matched_filenames(result: dict) -> set:
    return {Path(block["filename"]).name for block in result["blocks"]}


def test_finds_literal_matches_across_the_tree(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path)).apply({"dirname": "", "queries": ["hello"]})

    assert _matched_filenames(result) == {"top.py", "nested.py", "notes.txt"}
    assert result["match_count"] == 4
    assert result["truncated"] is False


def test_binary_files_are_skipped(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path)).apply({"dirname": "", "queries": ["hello"]})

    assert "binary.dat" not in _matched_filenames(result)


def test_case_insensitive_flag(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path)).apply(
        {"dirname": "", "queries": ["HELLO WORLD"], "case_insensitive": True})

    assert "top.py" in _matched_filenames(result)


def test_multiple_queries_match_on_any_hit(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path)).apply(
        {"dirname": "", "queries": ["import os", "from notes"]})

    assert _matched_filenames(result) == {"top.py", "notes.txt"}
    assert result["match_count"] == 2


def test_regex_pattern(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path)).apply(
        {"dirname": "", "queries": [r"^def \w+\(\):"], "is_regex": True})

    assert result["match_count"] == 1
    block = result["blocks"][0]
    assert block["filename"].endswith("nested.py")
    matched_lines = [line for line in block["lines"] if line["matched"]]
    assert len(matched_lines) == 1
    assert matched_lines[0]["line_number"] == 1


def test_multiple_regex_queries_are_distinct_alternatives(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path)).apply(
        {"dirname": "", "queries": [r"^def \w+\(\):", r"^import \w+"], "is_regex": True})

    assert result["match_count"] == 2
    assert _matched_filenames(result) == {"top.py", "nested.py"}


def test_invalid_regex_raises_value_error(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(ValueError, match="Invalid regex"):
        GrepTool(_context(tmp_path)).apply({"dirname": "", "queries": ["("], "is_regex": True})


def test_empty_queries_raises_value_error(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(ValueError, match="queries"):
        GrepTool(_context(tmp_path)).apply({"dirname": "", "queries": []})


def test_file_glob_filters_searched_files(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path)).apply(
        {"dirname": "", "queries": ["hello"], "file_glob": "*.py"})

    assert _matched_filenames(result) == {"top.py", "nested.py"}


def test_context_lines_surround_each_match(tmp_path: Path) -> None:
    (tmp_path / "lines.txt").write_text("a\nb\nMATCH\nc\nd\n")

    result = GrepTool(_context(tmp_path, context_lines=1)).apply(
        {"dirname": "", "queries": ["MATCH"]})

    assert len(result["blocks"]) == 1
    block = result["blocks"][0]
    assert block["start_line"] == 2
    assert block["end_line"] == 4
    assert [line["line"] for line in block["lines"]] == ["b", "MATCH", "c"]
    assert [line["matched"] for line in block["lines"]] == [False, True, False]


def test_adjacent_matches_merge_into_one_block(tmp_path: Path) -> None:
    (tmp_path / "lines.txt").write_text("MATCH\nx\nMATCH\n")

    result = GrepTool(_context(tmp_path, context_lines=1)).apply(
        {"dirname": "", "queries": ["MATCH"]})

    assert len(result["blocks"]) == 1
    block = result["blocks"][0]
    assert block["start_line"] == 1
    assert block["end_line"] == 3
    assert result["match_count"] == 2


def test_max_results_truncates(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path, max_results=1)).apply(
        {"dirname": "", "queries": ["hello"]})

    assert result["match_count"] == 1
    assert result["truncated"] is True


def test_max_results_exact_count_is_not_truncated(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path, max_results=4)).apply(
        {"dirname": "", "queries": ["hello"]})

    assert result["match_count"] == 4
    assert result["truncated"] is False


def test_denied_root_raises_permission_error(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(PermissionError):
        GrepTool(_context(tmp_path, read_dirs=DirRules(deny=[tmp_path]))).apply(
            {"dirname": "", "queries": ["hello"]})


def test_ask_root_raises_permission_ask_required(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(PermissionAskRequired):
        GrepTool(_context(tmp_path, read_dirs=DirRules(ask=[tmp_path]))).apply(
            {"dirname": "", "queries": ["hello"]})


def test_denied_subdir_is_skipped_not_raised(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path, read_dirs=DirRules(deny=[tmp_path / "sub"]))).apply(
        {"dirname": "", "queries": ["hello"]})

    assert _matched_filenames(result) == {"top.py"}


def test_summary_on_success(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    tool = GrepTool(_context(tmp_path))
    result = tool.apply({"dirname": "", "queries": ["hello"]})

    summary = tool.summary({"dirname": "", "queries": ["hello"]}, result)
    assert "hello" in summary
    assert "4 matches" in summary


def test_summary_on_failure_includes_the_error() -> None:
    tool = GrepTool(_context(Path("/tmp")))

    assert tool.summary({"dirname": "", "queries": ["hello"]}, error="not found") == (
        "Grep: ['hello'] failed: not found")
