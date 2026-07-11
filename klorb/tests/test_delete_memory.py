# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.memory.delete_memory."""

from pathlib import Path

import pytest

from klorb.permissions.table import PermissionAskRequired, Verdict
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.memory import common as memory_common_module
from klorb.tools.memory.common import Namespace, memory_namespace_dir
from klorb.tools.memory.delete_memory import DeleteMemoryTool
from klorb.tools.setup_context import ToolSetupContext
from klorb.workspace import Workspace


def _context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *,
    trusted: bool = True, delete_permission: Verdict = "allow",
) -> ToolSetupContext:
    monkeypatch.setattr(memory_common_module, "KLORB_DATA_DIR", tmp_path / "data")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    return ToolSetupContext(
        process_config=ProcessConfig(memory_delete_permission=delete_permission),
        session_config=SessionConfig(workspace=Workspace(path=workspace_root, trusted=trusted)))


def _write(context: ToolSetupContext, namespace: Namespace, filename: str, content: str) -> Path:
    namespace_dir = memory_namespace_dir(context, namespace)
    namespace_dir.mkdir(parents=True, exist_ok=True)
    path = namespace_dir / filename
    path.write_text(content)
    return path


def test_deletes_an_existing_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    path = _write(context, "global", "notes.md", "Topic\n")

    result = DeleteMemoryTool(context).apply({"namespace": "global", "filename": "notes.md"})

    assert not path.exists()
    assert result == {"namespace": "global", "filename": "notes.md", "deleted": True}


def test_nonexistent_filename_raises_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)

    with pytest.raises(FileNotFoundError, match="no such global memory"):
        DeleteMemoryTool(context).apply({"namespace": "global", "filename": "missing.md"})


def test_deleting_does_not_remove_the_namespace_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "notes.md", "Topic\n")
    namespace_dir = memory_namespace_dir(context, "global")

    DeleteMemoryTool(context).apply({"namespace": "global", "filename": "notes.md"})

    assert namespace_dir.is_dir()


def test_filename_traversal_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="path separator"):
        DeleteMemoryTool(context).apply({"namespace": "global", "filename": "../escape.md"})


def test_workspace_memory_in_untrusted_workspace_is_denied_before_touching_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, trusted=False)
    path = _write(context, "workspace", "notes.md", "Topic\n")

    with pytest.raises(PermissionError):
        DeleteMemoryTool(context).apply({"namespace": "workspace", "filename": "notes.md"})

    assert path.exists()


def test_delete_permission_defaults_to_ask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch, delete_permission="ask")
    path = _write(context, "global", "notes.md", "Topic\n")

    with pytest.raises(PermissionAskRequired):
        DeleteMemoryTool(context).apply({"namespace": "global", "filename": "notes.md"})

    assert path.exists()


def test_delete_permission_deny_raises_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, delete_permission="deny")
    path = _write(context, "global", "notes.md", "Topic\n")

    with pytest.raises(PermissionError):
        DeleteMemoryTool(context).apply({"namespace": "global", "filename": "notes.md"})

    assert path.exists()


def test_name_and_parameters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = DeleteMemoryTool(_context(tmp_path, monkeypatch))

    assert tool.name() == "DeleteMemory"
    assert set(tool.parameters()["required"]) == {"namespace", "filename"}


def test_summary_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "notes.md", "Topic\n")
    tool = DeleteMemoryTool(context)
    args = {"namespace": "global", "filename": "notes.md"}

    result = tool.apply(args)

    assert tool.summary(args, result) == "Delete memory: global/notes.md"


def test_summary_on_failure_includes_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = DeleteMemoryTool(_context(tmp_path, monkeypatch))
    args = {"namespace": "global", "filename": "notes.md"}

    assert tool.summary(args, error="not found") == "Delete memory: global/notes.md failed: not found"
