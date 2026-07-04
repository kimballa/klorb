# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.dir_walk."""

from pathlib import Path

import pytest

from klorb.permissions.directory_access import DirRules
from klorb.permissions.table import PermissionAskRequired
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.dir_walk import walk_readable_tree
from klorb.tools.setup_context import ToolSetupContext


def _context(
    workspace_root: Path,
    *,
    is_workspace_trusted: bool = False,
    read_dirs: DirRules | None = None,
) -> ToolSetupContext:
    return ToolSetupContext(
        process_config=ProcessConfig(is_workspace_trusted=is_workspace_trusted),
        session_config=SessionConfig(
            workspace_root=workspace_root,
            read_dirs=read_dirs or DirRules(),
            write_dirs=DirRules(),
        ),
    )


def _make_tree(root: Path) -> None:
    (root / "top.txt").write_text("top\n")
    (root / "sub_a").mkdir()
    (root / "sub_a" / "a.txt").write_text("a\n")
    (root / "sub_a" / "nested").mkdir()
    (root / "sub_a" / "nested" / "deep.txt").write_text("deep\n")
    (root / "sub_b").mkdir()
    (root / "sub_b" / "b.txt").write_text("b\n")


def test_walks_every_level_of_a_nested_tree(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    results = list(walk_readable_tree(_context(tmp_path), ""))

    visited = {str(dir_path.relative_to(tmp_path)): (subdirs, files) for dir_path, subdirs, files in results}
    assert visited["."] == (["sub_a", "sub_b"], ["top.txt"])
    assert visited["sub_a"] == (["nested"], ["a.txt"])
    assert visited["sub_a/nested"] == ([], ["deep.txt"])
    assert visited["sub_b"] == ([], ["b.txt"])


def test_missing_root_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list(walk_readable_tree(_context(tmp_path), "does_not_exist"))


def test_file_root_raises_not_a_directory(tmp_path: Path) -> None:
    file_path = tmp_path / "a.txt"
    file_path.write_text("a\n")

    with pytest.raises(NotADirectoryError):
        list(walk_readable_tree(_context(tmp_path), "a.txt"))


def test_denied_root_raises_permission_error(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(PermissionError):
        list(walk_readable_tree(_context(tmp_path, read_dirs=DirRules(deny=[tmp_path])), ""))


def test_ask_root_raises_permission_ask_required(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(PermissionAskRequired):
        list(walk_readable_tree(_context(tmp_path, read_dirs=DirRules(ask=[tmp_path])), ""))


def test_denied_subdir_is_pruned_not_raised(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    context = _context(tmp_path, read_dirs=DirRules(deny=[tmp_path / "sub_a"]))
    results = list(walk_readable_tree(context, ""))

    visited = {str(dir_path.relative_to(tmp_path)): (subdirs, files) for dir_path, subdirs, files in results}
    assert visited["."] == (["sub_b"], ["top.txt"])
    assert "sub_a" not in visited
    assert "sub_a/nested" not in visited
    assert visited["sub_b"] == ([], ["b.txt"])


def test_ask_subdir_is_pruned_not_raised(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    context = _context(tmp_path, read_dirs=DirRules(ask=[tmp_path / "sub_a"]))
    results = list(walk_readable_tree(context, ""))

    visited = {str(dir_path.relative_to(tmp_path)): (subdirs, files) for dir_path, subdirs, files in results}
    assert visited["."] == (["sub_b"], ["top.txt"])
    assert "sub_a" not in visited


def test_klorb_dir_is_implicitly_pruned(tmp_path: Path) -> None:
    (tmp_path / ".klorb").mkdir()
    (tmp_path / ".klorb" / "klorb-config.json").write_text("{}\n")
    (tmp_path / "top.txt").write_text("top\n")

    results = list(walk_readable_tree(_context(tmp_path), ""))

    visited = {str(dir_path.relative_to(tmp_path)) for dir_path, _, _ in results}
    assert visited == {"."}


def test_symlinked_subdir_is_not_descended(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    real_target = tmp_path / "outside"
    real_target.mkdir()
    (real_target / "secret.txt").write_text("secret\n")
    link = workspace / "link"
    link.symlink_to(real_target)

    results = list(walk_readable_tree(_context(workspace), ""))

    assert len(results) == 1
    dir_path, subdirs, files = results[0]
    assert dir_path == workspace.resolve()
    assert subdirs == []
    assert files == []


def test_symlinked_file_is_still_listed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    real_file = tmp_path / "real.txt"
    real_file.write_text("hi\n")
    link = workspace / "link.txt"
    link.symlink_to(real_file)

    results = list(walk_readable_tree(_context(workspace), ""))

    assert len(results) == 1
    _dir_path, _subdirs, files = results[0]
    assert files == ["link.txt"]
