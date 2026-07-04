# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.list_dir."""

from pathlib import Path

import pytest

from klorb.permissions.directory_access import DirRules
from klorb.permissions.table import PermissionAskRequired
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.list_dir import ListDirTool
from klorb.tools.setup_context import ToolSetupContext


def _context(
    workspace_root: Path,
    *,
    is_workspace_trusted: bool = False,
    read_dirs: DirRules | None = None,
    write_dirs: DirRules | None = None,
) -> ToolSetupContext:
    return ToolSetupContext(
        process_config=ProcessConfig(is_workspace_trusted=is_workspace_trusted),
        session_config=SessionConfig(
            workspace_root=workspace_root,
            read_dirs=read_dirs or DirRules(),
            write_dirs=write_dirs or DirRules(),
        ),
    )


def _make_tree(root: Path) -> None:
    (root / "sub_b").mkdir()
    (root / "sub_a").mkdir()
    (root / "b.txt").write_text("b\n")
    (root / "a.txt").write_text("a\n")


def test_empty_dirname_lists_project_root(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = ListDirTool(_context(tmp_path)).apply({"dirname": ""})

    assert result["cwd"] == str(tmp_path.resolve())
    assert result["subdirs"] == ["sub_a", "sub_b"]
    assert result["files"] == ["a.txt", "b.txt"]


def test_relative_dirname_resolves_against_workspace_root(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = ListDirTool(_context(tmp_path)).apply({"dirname": "sub_a"})

    assert result["cwd"] == str((tmp_path / "sub_a").resolve())
    assert result["subdirs"] == []
    assert result["files"] == []


def test_absolute_dirname_inside_workspace(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = ListDirTool(_context(tmp_path)).apply({"dirname": str(tmp_path)})

    assert result["subdirs"] == ["sub_a", "sub_b"]
    assert result["files"] == ["a.txt", "b.txt"]


def test_missing_directory_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ListDirTool(_context(tmp_path)).apply({"dirname": "does_not_exist"})


def test_file_path_raises_not_a_directory(tmp_path: Path) -> None:
    file_path = tmp_path / "a.txt"
    file_path.write_text("a\n")

    with pytest.raises(NotADirectoryError):
        ListDirTool(_context(tmp_path)).apply({"dirname": "a.txt"})


def test_name_and_parameters(tmp_path: Path) -> None:
    tool = ListDirTool(_context(tmp_path))

    assert tool.name() == "ListDir"
    assert tool.parameters()["required"] == ["dirname"]


# --- Permission-table integration (see docs/specs/permissions.md) ---


def test_zero_config_still_works_inside_workspace(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = ListDirTool(_context(tmp_path)).apply({"dirname": ""})

    assert result["subdirs"] == ["sub_a", "sub_b"]


def test_untrusted_zero_config_denies_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(PermissionError):
        ListDirTool(_context(workspace)).apply({"dirname": str(outside)})


def test_symlinked_escape_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    link = workspace / "link"
    link.symlink_to(outside_dir)

    with pytest.raises(PermissionError):
        ListDirTool(_context(workspace)).apply({"dirname": "link"})


def test_readdirs_deny_rejects_in_workspace_dir(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(PermissionError):
        ListDirTool(_context(tmp_path, read_dirs=DirRules(deny=[tmp_path]))).apply(
            {"dirname": ""})


def test_readdirs_ask_raises_permission_ask_required(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(PermissionAskRequired):
        ListDirTool(_context(tmp_path, read_dirs=DirRules(ask=[tmp_path]))).apply(
            {"dirname": ""})


def test_klorb_dir_read_implicitly_denied(tmp_path: Path) -> None:
    (tmp_path / ".klorb").mkdir()

    with pytest.raises(PermissionError):
        ListDirTool(_context(tmp_path, read_dirs=DirRules(allow=[tmp_path]))).apply(
            {"dirname": ".klorb"})


def test_trusted_empty_tables_deny_everything(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(PermissionError):
        ListDirTool(_context(tmp_path, is_workspace_trusted=True)).apply({"dirname": ""})


# --- summary() (see docs/specs/terminal-repl.md) ---


def test_summary_on_success_names_the_dir_and_counts(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    tool = ListDirTool(_context(tmp_path))
    result = tool.apply({"dirname": ""})

    assert tool.summary({"dirname": ""}, result) == (
        f"List dir: {tmp_path.resolve()} (2 dirs, 2 files)")


def test_summary_on_failure_includes_the_error() -> None:
    tool = ListDirTool(_context(Path("/tmp")))

    assert tool.summary({"dirname": "missing"}, error="not found") == (
        "List dir: missing failed: not found")
