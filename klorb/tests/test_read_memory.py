# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.memory.read_memory."""

from pathlib import Path

import pytest

from klorb.permissions.table import PermissionAskRequired, Verdict
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.memory import common as memory_common_module
from klorb.tools.memory.common import Namespace, memory_namespace_dir
from klorb.tools.memory.read_memory import ReadMemoryTool
from klorb.tools.setup_context import ToolSetupContext
from klorb.workspace import Workspace


def _context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *,
    trusted: bool = True, read_permission: Verdict = "allow",
) -> ToolSetupContext:
    monkeypatch.setattr(memory_common_module, "KLORB_DATA_DIR", tmp_path / "data")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    return ToolSetupContext(
        process_config=ProcessConfig(memory_read_permission=read_permission),
        session_config=SessionConfig(workspace=Workspace(path=workspace_root, trusted=trusted)))


def _write(context: ToolSetupContext, namespace: Namespace, filename: str, content: str) -> Path:
    namespace_dir = memory_namespace_dir(context, namespace)
    namespace_dir.mkdir(parents=True, exist_ok=True)
    path = namespace_dir / filename
    path.write_text(content)
    return path


def test_reads_the_whole_file_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "notes.md", "Topic\nline2\nline3\n")

    result = ReadMemoryTool(context).apply({"namespace": "global", "filename": "notes.md"})

    assert result["content"] == "1|Topic\n2|line2\n3|line3"
    assert result["total_lines"] == 3
    assert result["namespace"] == "global"
    assert result["filename"] == "notes.md"


def test_reads_a_workspace_memory_when_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, trusted=True)
    _write(context, "workspace", "notes.md", "Topic\n")

    result = ReadMemoryTool(context).apply({"namespace": "workspace", "filename": "notes.md"})

    assert result["namespace"] == "workspace"


def test_nonexistent_filename_raises_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)

    with pytest.raises(FileNotFoundError, match="no such global memory"):
        ReadMemoryTool(context).apply({"namespace": "global", "filename": "missing.md"})


def test_workspace_memory_in_untrusted_workspace_is_denied_without_reaching_core(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, trusted=False)
    _write(context, "workspace", "secret.md", "Topic\nsecret line\n")

    with pytest.raises(PermissionError) as exc_info:
        ReadMemoryTool(context).apply({"namespace": "workspace", "filename": "secret.md"})

    assert "secret line" not in str(exc_info.value)


def test_global_memory_unaffected_by_untrusted_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, trusted=False)
    _write(context, "global", "notes.md", "Topic\n")

    result = ReadMemoryTool(context).apply({"namespace": "global", "filename": "notes.md"})

    assert result["namespace"] == "global"


def test_read_permission_deny_raises_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, read_permission="deny")
    _write(context, "global", "notes.md", "Topic\n")

    with pytest.raises(PermissionError):
        ReadMemoryTool(context).apply({"namespace": "global", "filename": "notes.md"})


def test_read_permission_ask_raises_permission_ask_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, read_permission="ask")
    _write(context, "global", "notes.md", "Topic\n")

    with pytest.raises(PermissionAskRequired):
        ReadMemoryTool(context).apply({"namespace": "global", "filename": "notes.md"})


def test_read_permission_allow_succeeds_for_workspace_namespace_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, read_permission="allow", trusted=True)
    _write(context, "workspace", "notes.md", "Topic\n")

    ReadMemoryTool(context).apply({"namespace": "workspace", "filename": "notes.md"})  # no raise


def test_name_and_parameters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = ReadMemoryTool(_context(tmp_path, monkeypatch))

    assert tool.name() == "ReadMemory"
    assert set(tool.parameters()["required"]) == {"namespace", "filename"}


def test_summary_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "notes.md", "Topic\n")
    tool = ReadMemoryTool(context)
    args = {"namespace": "global", "filename": "notes.md"}

    result = tool.apply(args)

    assert tool.summary(args, result) == "Read memory: global/notes.md (lines 1-1 of 1)"


def test_summary_on_failure_includes_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = ReadMemoryTool(_context(tmp_path, monkeypatch))
    args = {"namespace": "global", "filename": "notes.md"}

    assert tool.summary(args, error="boom") == "Read memory: global/notes.md failed: boom"
