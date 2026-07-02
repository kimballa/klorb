# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.create_file."""

from pathlib import Path

import pytest

from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.create_file import CreateFileTool
from klorb.tools.setup_context import ToolSetupContext


def _context(workspace_root: Path) -> ToolSetupContext:
    return ToolSetupContext(
        process_config=ProcessConfig(), session_config=SessionConfig(workspace_root=workspace_root))


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
