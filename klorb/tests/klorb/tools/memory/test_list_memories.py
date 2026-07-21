# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.memory.list_memories."""

from pathlib import Path

import pytest

from klorb.permissions.table import PermissionAskRequired, Verdict
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.memory import common as memory_common_module
from klorb.tools.memory.common import memory_namespace_dir
from klorb.tools.memory.list_memories import ListMemoriesTool
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


def _write_global(context: ToolSetupContext, filename: str, content: str) -> Path:
    namespace_dir = memory_namespace_dir(context, "global")
    namespace_dir.mkdir(parents=True, exist_ok=True)
    path = namespace_dir / filename
    path.write_text(content)
    return path


def _write_workspace(context: ToolSetupContext, filename: str, content: str) -> Path:
    namespace_dir = memory_namespace_dir(context, "workspace")
    namespace_dir.mkdir(parents=True, exist_ok=True)
    path = namespace_dir / filename
    path.write_text(content)
    return path


def test_empty_namespaces_report_empty_lists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)

    result = ListMemoriesTool(context).apply({})

    assert result == {"global": [], "workspace": []}


def test_lists_filename_and_topic_for_each_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    _write_global(context, "a.md", "Global topic\nbody\n")
    _write_workspace(context, "b.md", "Workspace topic\nbody\n")

    result = ListMemoriesTool(context).apply({})

    assert result["global"] == [{"filename": "a.md", "topic": "Global topic"}]
    assert result["workspace"] == [{"filename": "b.md", "topic": "Workspace topic"}]


def test_zero_byte_memory_reports_empty_topic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    _write_global(context, "a.md", "")

    result = ListMemoriesTool(context).apply({})

    assert result["global"] == [{"filename": "a.md", "topic": ""}]


def test_whitespace_only_first_line_reports_empty_topic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    _write_global(context, "a.md", "   \nbody\n")

    result = ListMemoriesTool(context).apply({})

    assert result["global"] == [{"filename": "a.md", "topic": ""}]


def test_non_md_and_dotfiles_are_excluded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    namespace_dir = memory_namespace_dir(context, "global")
    namespace_dir.mkdir(parents=True)
    (namespace_dir / "notes.txt").write_text("not markdown\n")
    (namespace_dir / ".hidden.md").write_text("hidden\n")
    _write_global(context, "real.md", "Real topic\n")

    result = ListMemoriesTool(context).apply({})

    assert result["global"] == [{"filename": "real.md", "topic": "Real topic"}]


def test_listing_does_not_recurse_into_subdirectories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    namespace_dir = memory_namespace_dir(context, "global")
    (namespace_dir / "subdir").mkdir(parents=True)
    (namespace_dir / "subdir" / "nested.md").write_text("Nested\n")

    result = ListMemoriesTool(context).apply({})

    assert result["global"] == []


def test_untrusted_workspace_reports_workspace_as_empty_without_touching_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, trusted=False)
    namespace_dir = memory_namespace_dir(context, "workspace")
    namespace_dir.mkdir(parents=True)
    (namespace_dir / "secret.md").write_text("Secret\n")
    _write_global(context, "a.md", "Global topic\n")

    result = ListMemoriesTool(context).apply({})

    assert result["workspace"] == []
    assert result["global"] == [{"filename": "a.md", "topic": "Global topic"}]


def test_read_permission_deny_raises_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, read_permission="deny")

    with pytest.raises(PermissionError):
        ListMemoriesTool(context).apply({})


def test_read_permission_ask_raises_permission_ask_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, read_permission="ask")

    with pytest.raises(PermissionAskRequired):
        ListMemoriesTool(context).apply({})


def test_name_and_parameters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = ListMemoriesTool(_context(tmp_path, monkeypatch))

    assert tool.name() == "ListMemories"
    assert tool.parameters()["required"] == []


def test_summary_reports_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    _write_global(context, "a.md", "Topic\n")
    tool = ListMemoriesTool(context)

    result = tool.apply({})

    assert tool.summary({}, result) == "List memories (1 global, 0 workspace)"


def test_summary_on_failure_includes_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = ListMemoriesTool(_context(tmp_path, monkeypatch))

    assert tool.summary({}, error="boom") == "List memories failed: boom"
