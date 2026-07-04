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


def _context(
    workspace_root: Path,
    *,
    max_results: int = 500,
    read_dirs: DirRules | None = None,
) -> ToolSetupContext:
    return ToolSetupContext(
        process_config=ProcessConfig(grep_max_results=max_results),
        session_config=SessionConfig(
            workspace_root=workspace_root,
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


def test_finds_literal_matches_across_the_tree(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path)).apply({"dirname": "", "pattern": "hello"})

    filenames = {Path(m["filename"]).name for m in result["matches"]}
    assert filenames == {"top.py", "nested.py", "notes.txt"}
    assert result["truncated"] is False


def test_binary_files_are_skipped(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path)).apply({"dirname": "", "pattern": "hello"})

    assert all(not m["filename"].endswith("binary.dat") for m in result["matches"])


def test_case_insensitive_flag(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path)).apply(
        {"dirname": "", "pattern": "HELLO WORLD", "case_insensitive": True})

    assert any(m["filename"].endswith("top.py") for m in result["matches"])


def test_regex_pattern(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path)).apply(
        {"dirname": "", "pattern": r"^def \w+\(\):", "is_regex": True})

    assert len(result["matches"]) == 1
    assert result["matches"][0]["filename"].endswith("nested.py")
    assert result["matches"][0]["line_number"] == 1


def test_invalid_regex_raises_value_error(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(ValueError, match="Invalid regex"):
        GrepTool(_context(tmp_path)).apply({"dirname": "", "pattern": "(", "is_regex": True})


def test_file_glob_filters_searched_files(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path)).apply(
        {"dirname": "", "pattern": "hello", "file_glob": "*.py"})

    filenames = {Path(m["filename"]).name for m in result["matches"]}
    assert filenames == {"top.py", "nested.py"}


def test_max_results_truncates(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path, max_results=1)).apply({"dirname": "", "pattern": "hello"})

    assert len(result["matches"]) == 1
    assert result["truncated"] is True


def test_denied_root_raises_permission_error(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(PermissionError):
        GrepTool(_context(tmp_path, read_dirs=DirRules(deny=[tmp_path]))).apply(
            {"dirname": "", "pattern": "hello"})


def test_ask_root_raises_permission_ask_required(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(PermissionAskRequired):
        GrepTool(_context(tmp_path, read_dirs=DirRules(ask=[tmp_path]))).apply(
            {"dirname": "", "pattern": "hello"})


def test_denied_subdir_is_skipped_not_raised(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path, read_dirs=DirRules(deny=[tmp_path / "sub"]))).apply(
        {"dirname": "", "pattern": "hello"})

    filenames = {Path(m["filename"]).name for m in result["matches"]}
    assert filenames == {"top.py"}


def test_summary_on_success(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    tool = GrepTool(_context(tmp_path))
    result = tool.apply({"dirname": "", "pattern": "hello"})

    summary = tool.summary({"dirname": "", "pattern": "hello"}, result)
    assert "hello" in summary
    assert "4 matches" in summary


def test_summary_on_failure_includes_the_error() -> None:
    tool = GrepTool(_context(Path("/tmp")))

    assert tool.summary({"dirname": "", "pattern": "hello"}, error="not found") == (
        "Grep: 'hello' failed: not found")
