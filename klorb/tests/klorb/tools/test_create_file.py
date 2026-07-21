# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.create_file."""

from pathlib import Path

import pytest

from klorb.permissions.directory_access import DirRules
from klorb.permissions.table import PermissionAskRequired
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.create_file import CreateFileTool
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.util import CreateFileCore
from klorb.workspace import Workspace


def _context(
    workspace_root: Path, *, read_dirs: DirRules | None = None, write_dirs: DirRules | None = None,
) -> ToolSetupContext:
    """Defaults both `readDirs`/`writeDirs` to allowing all of `workspace_root`, since
    `evaluate_write()` requires an explicit allow in *both* tables (see
    docs/adrs/write-verdict-is-stricter-of-read-and-write-tables.md) and most tests here are
    about CreateFile's own logic, not the permission system -- only the "Permission-table
    integration" tests below pass an explicit override to narrow that default."""
    return ToolSetupContext(
        process_config=ProcessConfig(),
        session_config=SessionConfig(
            workspace=Workspace(path=workspace_root),
            read_dirs=read_dirs or DirRules(allow=[workspace_root]),
            write_dirs=write_dirs or DirRules(allow=[workspace_root])))


def test_creates_a_new_file(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"

    result = CreateFileTool(_context(tmp_path)).apply(
        {"filename": str(file_path), "content": "a\nb\nc\n"})

    assert file_path.read_text() == "a\nb\nc\n"
    assert result["created"] is True
    assert result["total_lines"] == 3


def test_creates_an_empty_file(tmp_path: Path) -> None:
    file_path = tmp_path / "empty.txt"

    result = CreateFileTool(_context(tmp_path)).apply({"filename": str(file_path), "content": ""})

    assert file_path.read_text() == ""
    assert result["total_lines"] == 0


def test_raises_if_file_already_exists(tmp_path: Path) -> None:
    file_path = tmp_path / "existing.txt"
    file_path.write_text("old\n")

    with pytest.raises(FileExistsError, match="already exists"):
        CreateFileTool(_context(tmp_path)).apply({"filename": str(file_path), "content": "new\n"})

    assert file_path.read_text() == "old\n"


def test_creates_missing_parent_directories(tmp_path: Path) -> None:
    file_path = tmp_path / "a" / "b" / "c" / "new.txt"

    CreateFileTool(_context(tmp_path)).apply({"filename": str(file_path), "content": "hi\n"})

    assert file_path.read_text() == "hi\n"


def test_path_outside_workspace_root_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"

    with pytest.raises(PermissionError):
        CreateFileTool(_context(workspace)).apply({"filename": str(outside), "content": "x\n"})

    assert not outside.exists()


def test_symlinked_parent_directory_escape_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    link = workspace / "link"
    link.symlink_to(outside_dir)

    with pytest.raises(PermissionError):
        CreateFileTool(_context(workspace)).apply(
            {"filename": str(link / "new.txt"), "content": "x\n"})

    assert not (outside_dir / "new.txt").exists()


def test_name_and_parameters(tmp_path: Path) -> None:
    tool = CreateFileTool(_context(tmp_path))

    assert tool.name() == "CreateFile"
    assert set(tool.parameters()["required"]) == {"filename", "content"}


def test_delegates_file_creation_to_create_file_core(tmp_path: Path) -> None:
    tool = CreateFileTool(_context(tmp_path))
    assert isinstance(tool.create_file_core, CreateFileCore)


# --- Permission-table integration (see docs/specs/permissions.md) ---


def test_writedirs_deny_rejects_an_otherwise_in_workspace_write(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"

    with pytest.raises(PermissionError):
        CreateFileTool(_context(tmp_path, write_dirs=DirRules(deny=[tmp_path]))).apply(
            {"filename": str(file_path), "content": "x\n"})

    assert not file_path.exists()


def test_writedirs_ask_raises_permission_ask_required(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"

    with pytest.raises(PermissionAskRequired):
        CreateFileTool(_context(tmp_path, write_dirs=DirRules(ask=[tmp_path]))).apply(
            {"filename": str(file_path), "content": "x\n"})

    assert not file_path.exists()


def test_hard_workspace_boundary_wins_even_if_writedirs_allow_covers_outside(tmp_path: Path) -> None:
    """writeDirs.allow can only ever narrow access within workspace_root, never widen past the
    hard resolve_within_workspace() boundary."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"

    with pytest.raises(PermissionError):
        CreateFileTool(_context(workspace, write_dirs=DirRules(allow=[tmp_path]))).apply(
            {"filename": str(outside), "content": "x\n"})

    assert not outside.exists()


def test_writedirs_allow_alone_does_not_grant_write_without_readdirs_allow(tmp_path: Path) -> None:
    """writeDirs.allow alone does not grant write access to a path readDirs is silent on --
    write access is never more permissive than read access for the same path; see
    docs/adrs/write-verdict-is-stricter-of-read-and-write-tables.md. An integration-level
    counterpart to test_permissions.py's unit-level
    test_evaluate_write_asks_when_writedirs_allows_but_readdirs_is_silent, since this file's
    other permission tests all use the (allow, allow) default context."""
    file_path = tmp_path / "new.txt"

    with pytest.raises(PermissionAskRequired):
        CreateFileTool(_context(
            tmp_path, read_dirs=DirRules(), write_dirs=DirRules(allow=[tmp_path]),
        )).apply({"filename": str(file_path), "content": "x\n"})

    assert not file_path.exists()


def test_klorb_dir_write_implicitly_denied_even_with_no_config(tmp_path: Path) -> None:
    """${workspace_root}/.klorb/ is implicitly write-denied regardless of writeDirs config —
    the agent must not be able to rewrite its own permission grants."""
    file_path = tmp_path / ".klorb" / "klorb-config.json"

    with pytest.raises(PermissionError):
        CreateFileTool(_context(tmp_path)).apply({"filename": str(file_path), "content": "{}"})

    assert not file_path.exists()


def test_klorb_dir_write_denied_even_with_writedirs_allow_covering_whole_workspace(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / ".klorb" / "klorb-config.json"

    with pytest.raises(PermissionError):
        CreateFileTool(_context(tmp_path, write_dirs=DirRules(allow=[tmp_path]))).apply(
            {"filename": str(file_path), "content": "{}"})

    assert not file_path.exists()


# --- summary() (see docs/specs/terminal-repl.md) ---


def test_summary_on_success_names_the_file_and_line_count(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"
    tool = CreateFileTool(_context(tmp_path))
    args = {"filename": str(file_path), "content": "a\nb\nc\n"}

    result = tool.apply(args)

    assert tool.summary(args, result) == f"Create file: {file_path} (3 lines)"


def test_summary_on_failure_includes_the_error() -> None:
    tool = CreateFileTool(_context(Path("/tmp")))

    assert tool.summary({"filename": "existing.txt"}, error="already exists") == (
        "Create file: existing.txt failed: already exists")


def test_diff_preview_is_an_all_add_hunk(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"
    tool = CreateFileTool(_context(tmp_path))
    args = {"filename": str(file_path), "content": "a\nb\n"}

    result = tool.apply(args)
    preview = tool.diff_preview(args, result)

    assert preview is not None
    assert preview.label == tool.summary(args, result)
    kinds = [line.kind for hunk in preview.hunks for line in hunk.lines]
    assert kinds == ["add", "add"]


def test_diff_preview_is_none_on_failure(tmp_path: Path) -> None:
    tool = CreateFileTool(_context(tmp_path))

    assert tool.diff_preview({"filename": "existing.txt"}, None, "already exists") is None
