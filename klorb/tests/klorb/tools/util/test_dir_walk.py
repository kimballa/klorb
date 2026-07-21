# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.util.dir_walk."""

from pathlib import Path

import pytest

from klorb.permissions.directory_access import DirRules
from klorb.permissions.table import PermissionAskRequired
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.util import walk_readable_tree
from klorb.workspace import Workspace


def _context(
    workspace_root: Path,
    *,
    is_workspace_trusted: bool = False,
    read_dirs: DirRules | None = None,
) -> ToolSetupContext:
    return ToolSetupContext(
        process_config=ProcessConfig(),
        session_config=SessionConfig(
            workspace=Workspace(path=workspace_root, trusted=is_workspace_trusted),
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


def _visited(
    results: list[tuple[Path, list[str], list[str], list[str]]], root: Path,
) -> dict[str, tuple[list[str], list[str], list[str]]]:
    """`{relative_dir: (subdir_names, file_names, gitignored_file_names)}` for `results`."""
    return {
        str(dir_path.relative_to(root)): (subdirs, files, ignored)
        for dir_path, subdirs, files, ignored in results
    }


def test_walks_every_level_of_a_nested_tree(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    results = list(walk_readable_tree(_context(tmp_path), ""))

    visited = _visited(results, tmp_path)
    assert visited["."] == (["sub_a", "sub_b"], ["top.txt"], [])
    assert visited["sub_a"] == (["nested"], ["a.txt"], [])
    assert visited["sub_a/nested"] == ([], ["deep.txt"], [])
    assert visited["sub_b"] == ([], ["b.txt"], [])


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

    visited = _visited(results, tmp_path)
    assert visited["."] == (["sub_b"], ["top.txt"], [])
    assert "sub_a" not in visited
    assert "sub_a/nested" not in visited
    assert visited["sub_b"] == ([], ["b.txt"], [])


def test_ask_subdir_is_pruned_not_raised(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    context = _context(tmp_path, read_dirs=DirRules(ask=[tmp_path / "sub_a"]))
    results = list(walk_readable_tree(context, ""))

    visited = _visited(results, tmp_path)
    assert visited["."] == (["sub_b"], ["top.txt"], [])
    assert "sub_a" not in visited


def test_klorb_dir_is_implicitly_pruned(tmp_path: Path) -> None:
    (tmp_path / ".klorb").mkdir()
    (tmp_path / ".klorb" / "klorb-config.json").write_text("{}\n")
    (tmp_path / "top.txt").write_text("top\n")

    results = list(walk_readable_tree(_context(tmp_path), ""))

    visited = {str(dir_path.relative_to(tmp_path)) for dir_path, _, _, _ in results}
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
    dir_path, subdirs, files, ignored = results[0]
    assert dir_path == workspace.resolve()
    assert subdirs == []
    assert files == []
    assert ignored == []


def test_gitignored_file_is_surfaced_separately_not_dropped(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    (tmp_path / ".gitignore").write_text("*.log\n")
    (tmp_path / "debug.log").write_text("noise\n")

    results = list(walk_readable_tree(_context(tmp_path), ""))

    subdirs, files, ignored = _visited(results, tmp_path)["."]
    assert "debug.log" not in files
    assert "top.txt" in files
    assert "debug.log" in ignored


def test_gitignored_subdir_is_still_descended_but_its_files_are_gitignored(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    (tmp_path / ".gitignore").write_text("sub_a/\n")

    results = list(walk_readable_tree(_context(tmp_path), ""))

    visited = _visited(results, tmp_path)
    # The ignored dir is dropped from its parent's kept subdirs...
    assert visited["."][0] == ["sub_b"]
    # ...but is still walked, with every file inside it reported as gitignored.
    assert visited["sub_a"] == ([], [], ["a.txt"])
    assert visited["sub_a/nested"] == ([], [], ["deep.txt"])


def test_use_gitignore_false_walks_everything_with_no_gitignored_files(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    (tmp_path / ".gitignore").write_text("*.log\nsub_a/\n")
    (tmp_path / "debug.log").write_text("noise\n")

    results = list(walk_readable_tree(_context(tmp_path), "", use_gitignore=False))

    visited = _visited(results, tmp_path)
    assert "debug.log" in visited["."][1]
    assert "sub_a" in visited["."][0]
    assert "sub_a" in visited
    # Nothing is ever reported as gitignored when the filter is off.
    assert all(ignored == [] for _subdirs, _files, ignored in visited.values())


def test_nested_gitignore_negation_reincludes(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    (tmp_path / ".gitignore").write_text("*.txt\n")
    (tmp_path / "sub_a" / ".gitignore").write_text("!a.txt\n")

    results = list(walk_readable_tree(_context(tmp_path), ""))

    visited = _visited(results, tmp_path)
    # Root-level `*.txt` hides top.txt, but the nested .gitignore re-includes sub_a/a.txt.
    assert "top.txt" not in visited["."][1]
    assert "top.txt" in visited["."][2]
    assert "a.txt" in visited["sub_a"][1]
    assert "a.txt" not in visited["sub_a"][2]


def test_ancestor_gitignore_applies_to_subdir_search(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    (tmp_path / ".gitignore").write_text("*.txt\n")

    results = list(walk_readable_tree(_context(tmp_path), "sub_a"))

    visited = _visited(results, tmp_path)
    # a.txt lives under the searched subdir but is hidden by the workspace-root .gitignore.
    assert visited["sub_a"][1] == []
    assert visited["sub_a"][2] == ["a.txt"]


def test_dot_git_subdir_is_skipped_unconditionally(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]\n")
    (git_dir / "objects").mkdir()
    (git_dir / "objects" / "pack").write_text("binary\n")

    results = list(walk_readable_tree(_context(tmp_path), ""))

    visited = _visited(results, tmp_path)
    assert ".git" not in visited["."][0]
    assert ".git" not in visited
    assert ".git/objects" not in visited


def test_dot_git_subdir_is_skipped_even_with_gitignore_disabled(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]\n")

    results = list(walk_readable_tree(_context(tmp_path), "", use_gitignore=False))

    visited = _visited(results, tmp_path)
    assert ".git" not in visited["."][0]
    assert ".git" not in visited
    # Skipping .git never counts as a gitignored file, even with gitignore filtering off.
    assert all(ignored == [] for _subdirs, _files, ignored in visited.values())


def test_dot_git_skip_never_sets_gitignored_files(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]\n")

    results = list(walk_readable_tree(_context(tmp_path), ""))

    visited = _visited(results, tmp_path)
    assert visited["."][2] == []


def test_explicit_dot_git_root_is_searched_normally(tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]\n")
    (git_dir / "objects").mkdir()
    (git_dir / "objects" / "pack").write_text("binary\n")

    results = list(walk_readable_tree(_context(tmp_path), ".git"))

    visited = _visited(results, git_dir)
    assert visited["."] == (["objects"], ["config"], [])
    assert visited["objects"] == ([], ["pack"], [])


def test_symlinked_file_is_still_listed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    real_file = tmp_path / "real.txt"
    real_file.write_text("hi\n")
    link = workspace / "link.txt"
    link.symlink_to(real_file)

    results = list(walk_readable_tree(_context(workspace), ""))

    assert len(results) == 1
    _dir_path, _subdirs, files, ignored = results[0]
    assert files == ["link.txt"]
    assert ignored == []
