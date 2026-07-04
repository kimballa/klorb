# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.find_file."""

from pathlib import Path

import pytest

from klorb.permissions.directory_access import DirRules
from klorb.permissions.table import PermissionAskRequired
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.find_file import FindFileTool
from klorb.tools.setup_context import ToolSetupContext


def _context(
    workspace_root: Path,
    *,
    max_results: int = 500,
    read_dirs: DirRules | None = None,
) -> ToolSetupContext:
    return ToolSetupContext(
        process_config=ProcessConfig(find_file_max_results=max_results),
        session_config=SessionConfig(
            workspace_root=workspace_root,
            read_dirs=read_dirs or DirRules(),
            write_dirs=DirRules(),
        ),
    )


def _make_tree(root: Path) -> None:
    (root / "top.py").write_text("top\n")
    (root / "readme.md").write_text("readme\n")
    (root / "sub").mkdir()
    (root / "sub" / "nested.py").write_text("nested\n")
    (root / "sub" / "deep").mkdir()
    (root / "sub" / "deep" / "leaf_context.py").write_text("leaf\n")


def test_finds_files_matching_glob_across_the_tree(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = FindFileTool(_context(tmp_path)).apply({"dirname": "", "pattern": "*.py"})

    names = {Path(m).name for m in result["matches"]}
    assert names == {"top.py", "nested.py", "leaf_context.py"}
    assert result["truncated"] is False


def test_substring_glob_pattern(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = FindFileTool(_context(tmp_path)).apply({"dirname": "", "pattern": "*_context*"})

    names = {Path(m).name for m in result["matches"]}
    assert names == {"leaf_context.py"}


def test_exact_name_pattern_matches_only_that_name(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = FindFileTool(_context(tmp_path)).apply({"dirname": "", "pattern": "readme.md"})

    names = {Path(m).name for m in result["matches"]}
    assert names == {"readme.md"}


def test_case_insensitive_flag(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = FindFileTool(_context(tmp_path)).apply(
        {"dirname": "", "pattern": "*.PY", "case_insensitive": True})

    names = {Path(m).name for m in result["matches"]}
    assert names == {"top.py", "nested.py", "leaf_context.py"}


def test_max_results_truncates(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = FindFileTool(_context(tmp_path, max_results=1)).apply(
        {"dirname": "", "pattern": "*.py"})

    assert len(result["matches"]) == 1
    assert result["truncated"] is True


def test_denied_root_raises_permission_error(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(PermissionError):
        FindFileTool(_context(tmp_path, read_dirs=DirRules(deny=[tmp_path]))).apply(
            {"dirname": "", "pattern": "*.py"})


def test_ask_root_raises_permission_ask_required(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(PermissionAskRequired):
        FindFileTool(_context(tmp_path, read_dirs=DirRules(ask=[tmp_path]))).apply(
            {"dirname": "", "pattern": "*.py"})


def test_denied_subdir_is_skipped_not_raised(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = FindFileTool(_context(tmp_path, read_dirs=DirRules(deny=[tmp_path / "sub"]))).apply(
        {"dirname": "", "pattern": "*.py"})

    names = {Path(m).name for m in result["matches"]}
    assert names == {"top.py"}


def test_summary_on_success(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    tool = FindFileTool(_context(tmp_path))
    result = tool.apply({"dirname": "", "pattern": "*.py"})

    summary = tool.summary({"dirname": "", "pattern": "*.py"}, result)
    assert "*.py" in summary
    assert "3 matches" in summary


def test_summary_on_failure_includes_the_error() -> None:
    tool = FindFileTool(_context(Path("/tmp")))

    assert tool.summary({"dirname": "", "pattern": "*.py"}, error="not found") == (
        "Find file: '*.py' failed: not found")
