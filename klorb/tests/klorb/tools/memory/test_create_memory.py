# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.memory.create_memory."""

from pathlib import Path

import pytest

from klorb.permissions.table import PermissionAskRequired, Verdict
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.memory import common as memory_common_module
from klorb.tools.memory.common import memory_namespace_dir
from klorb.tools.memory.create_memory import CreateMemoryTool
from klorb.tools.setup_context import ToolSetupContext
from klorb.workspace import Workspace


def _context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *,
    trusted: bool = True, create_permission: Verdict = "allow",
) -> ToolSetupContext:
    monkeypatch.setattr(memory_common_module, "KLORB_DATA_DIR", tmp_path / "data")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    return ToolSetupContext(
        process_config=ProcessConfig(memory_create_permission=create_permission),
        session_config=SessionConfig(workspace=Workspace(path=workspace_root, trusted=trusted)))


def test_creates_a_new_memory_auto_creating_the_namespace_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    namespace_dir = memory_namespace_dir(context, "global")
    assert not namespace_dir.exists()

    result = CreateMemoryTool(context).apply({
        "namespace": "global", "filename": "notes.md", "content": "Topic\nbody\n",
    })

    assert (namespace_dir / "notes.md").read_text() == "Topic\nbody\n"
    assert result["namespace"] == "global"
    assert result["filename"] == "notes.md"
    assert result["total_lines"] == 2
    assert result["created"] is True


def test_raises_if_memory_already_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    namespace_dir = memory_namespace_dir(context, "global")
    namespace_dir.mkdir(parents=True)
    (namespace_dir / "notes.md").write_text("Old topic\n")

    with pytest.raises(FileExistsError, match="use EditMemory to modify it"):
        CreateMemoryTool(context).apply({
            "namespace": "global", "filename": "notes.md", "content": "New topic\n",
        })

    assert (namespace_dir / "notes.md").read_text() == "Old topic\n"


def test_empty_content_is_rejected_up_front(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    namespace_dir = memory_namespace_dir(context, "global")

    with pytest.raises(ValueError, match="must not be blank"):
        CreateMemoryTool(context).apply({
            "namespace": "global", "filename": "notes.md", "content": "",
        })

    assert not namespace_dir.exists()


def test_whitespace_only_first_line_is_rejected_up_front(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="must not be blank"):
        CreateMemoryTool(context).apply({
            "namespace": "global", "filename": "notes.md", "content": "   \nbody\n",
        })


def test_filename_traversal_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="path separator"):
        CreateMemoryTool(context).apply({
            "namespace": "global", "filename": "../escape.md", "content": "Topic\n",
        })


def test_workspace_memory_in_untrusted_workspace_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, trusted=False)
    namespace_dir = memory_namespace_dir(context, "workspace")

    with pytest.raises(PermissionError):
        CreateMemoryTool(context).apply({
            "namespace": "workspace", "filename": "notes.md", "content": "Topic\n",
        })

    assert not namespace_dir.exists()


def test_create_permission_defaults_to_ask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch, create_permission="ask")

    with pytest.raises(PermissionAskRequired):
        CreateMemoryTool(context).apply({
            "namespace": "global", "filename": "notes.md", "content": "Topic\n",
        })


def test_create_permission_deny_raises_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, create_permission="deny")

    with pytest.raises(PermissionError):
        CreateMemoryTool(context).apply({
            "namespace": "global", "filename": "notes.md", "content": "Topic\n",
        })


def test_create_permission_allow_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch, create_permission="allow")

    CreateMemoryTool(context).apply({
        "namespace": "global", "filename": "notes.md", "content": "Topic\n",
    })  # no raise


def test_name_and_parameters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = CreateMemoryTool(_context(tmp_path, monkeypatch))

    assert tool.name() == "CreateMemory"
    assert set(tool.parameters()["required"]) == {"namespace", "filename", "content"}


def test_summary_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    tool = CreateMemoryTool(context)
    args = {"namespace": "global", "filename": "notes.md", "content": "Topic\nbody\n"}

    result = tool.apply(args)

    assert tool.summary(args, result) == "Create memory: global/notes.md (2 lines)"


def test_summary_on_failure_includes_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = CreateMemoryTool(_context(tmp_path, monkeypatch))
    args = {"namespace": "global", "filename": "notes.md"}

    assert tool.summary(args, error="already exists") == (
        "Create memory: global/notes.md failed: already exists")


def test_diff_preview_is_an_all_add_hunk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = CreateMemoryTool(_context(tmp_path, monkeypatch))
    args = {"namespace": "global", "filename": "notes.md", "content": "Topic\nbody\n"}

    result = tool.apply(args)
    preview = tool.diff_preview(args, result)

    assert preview is not None
    assert preview.label == tool.summary(args, result)
    kinds = [line.kind for hunk in preview.hunks for line in hunk.lines]
    assert kinds == ["add", "add"]
