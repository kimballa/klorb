# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.memory.edit_memory."""

from pathlib import Path

import pytest

from klorb.permissions.table import PermissionAskRequired, Verdict
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.memory import common as memory_common_module
from klorb.tools.memory.common import Namespace, memory_namespace_dir
from klorb.tools.memory.edit_memory import EditMemoryTool
from klorb.tools.setup_context import ToolSetupContext
from klorb.workspace import Workspace


def _context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *,
    trusted: bool = True, write_permission: Verdict = "allow",
) -> ToolSetupContext:
    monkeypatch.setattr(memory_common_module, "get_klorb_data_dir", lambda: tmp_path / "data")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    return ToolSetupContext(
        process_config=ProcessConfig(),
        session_config=SessionConfig(
            workspace=Workspace(path=workspace_root, trusted=trusted),
            memory_write_permission=write_permission))


def _write(context: ToolSetupContext, namespace: Namespace, filename: str, content: str) -> Path:
    namespace_dir = memory_namespace_dir(context, namespace)
    namespace_dir.mkdir(parents=True, exist_ok=True)
    path = namespace_dir / filename
    path.write_text(content)
    return path


def test_replaces_a_multiline_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    path = _write(context, "global", "notes.md", "Topic\nb\nc\nd\n")

    result = EditMemoryTool(context).apply({
        "namespace": "global", "filename": "notes.md",
        "old_text": "b\nc", "new_text": "B\nC",
    })

    assert path.read_text() == "Topic\nB\nC\nd\n"
    assert result["namespace"] == "global"
    assert result["filename"] == "notes.md"


def test_old_text_not_found_names_read_memory_reread_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "notes.md", "Topic\n" + "\n".join(f"L{i}" for i in range(1, 11)) + "\n")

    with pytest.raises(ValueError, match="re-ReadMemory global/notes.md"):
        EditMemoryTool(context).apply({
            "namespace": "global", "filename": "notes.md",
            "old_text": "NOPE", "new_text": "FIVE",
        })


def test_edit_targeting_line_one_directly_with_blank_replacement_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacing (not deleting) line 1 with a whitespace-only line leaves the file with a
    blank first line -- the line still exists, so this exercises the "edit targets line 1
    directly" case distinctly from deleting it (see the next test)."""
    context = _context(tmp_path, monkeypatch)
    path = _write(context, "global", "notes.md", "Topic\nbody\n")

    with pytest.raises(ValueError, match="must not be blank"):
        EditMemoryTool(context).apply({
            "namespace": "global", "filename": "notes.md",
            "old_text": "Topic", "new_text": "   ",
        })

    assert path.read_text() == "Topic\nbody\n"


def test_deleting_line_one_promoting_a_blank_line_two_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    path = _write(context, "global", "notes.md", "Topic\n\nbody\n")

    with pytest.raises(ValueError, match="must not be blank"):
        EditMemoryTool(context).apply({
            "namespace": "global", "filename": "notes.md",
            "old_text": "Topic", "new_text": "",
        })

    assert path.read_text() == "Topic\n\nbody\n"


def test_edit_leaving_a_non_blank_first_line_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    path = _write(context, "global", "notes.md", "Topic\nbody\n")

    EditMemoryTool(context).apply({
        "namespace": "global", "filename": "notes.md",
        "old_text": "Topic", "new_text": "New topic",
    })

    assert path.read_text() == "New topic\nbody\n"


def test_nonexistent_filename_raises_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shape other than old_text="" can't auto-create a memory (see
    test_edit_memory_auto_creates_nonexistent_memory_via_empty_old_text); it fails and names
    CreateMemory as the tool to use instead."""
    context = _context(tmp_path, monkeypatch)

    with pytest.raises(FileNotFoundError, match="does not exist; use CreateMemory"):
        EditMemoryTool(context).apply({
            "namespace": "global", "filename": "missing.md",
            "old_text": "a", "new_text": "b",
        })


def test_edit_memory_auto_creates_nonexistent_memory_via_empty_old_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A namespace/filename pair with nothing on disk (not even the namespace directory) is
    treated exactly like an existing-but-empty memory for old_text="" -- no prior CreateMemory
    call needed."""
    context = _context(tmp_path, monkeypatch)
    path = memory_namespace_dir(context, "global") / "notes.md"
    assert not path.exists()

    result = EditMemoryTool(context).apply({
        "namespace": "global", "filename": "notes.md",
        "old_text": "", "new_text": "Topic\nBody",
    })

    assert path.read_text() == "Topic\nBody\n"
    assert result["created"] is True


def test_edit_memory_auto_create_rolled_back_when_first_line_would_be_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A freshly auto-created memory that would end up with a blank first line has no pre-edit
    content to restore -- the just-created file is deleted instead, so no topic-less memory is
    left on disk even transiently."""
    context = _context(tmp_path, monkeypatch)
    path = memory_namespace_dir(context, "global") / "notes.md"

    with pytest.raises(ValueError, match="first line is its topic and must not be blank"):
        EditMemoryTool(context).apply({
            "namespace": "global", "filename": "notes.md",
            "old_text": "", "new_text": "   ",
        })

    assert not path.exists()


def test_workspace_memory_in_untrusted_workspace_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, trusted=False)
    path = _write(context, "workspace", "notes.md", "Topic\nbody\n")

    with pytest.raises(PermissionError):
        EditMemoryTool(context).apply({
            "namespace": "workspace", "filename": "notes.md",
            "old_text": "body", "new_text": "new body",
        })

    assert path.read_text() == "Topic\nbody\n"


def test_global_namespace_is_always_allowed_regardless_of_write_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, write_permission="deny")
    path = _write(context, "global", "notes.md", "Topic\n")

    EditMemoryTool(context).apply({
        "namespace": "global", "filename": "notes.md",
        "old_text": "Topic", "new_text": "New topic",
    })  # no raise, despite write_permission="deny"

    assert path.read_text() == "New topic\n"


def test_workspace_write_permission_deny_raises_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, write_permission="deny")
    _write(context, "workspace", "notes.md", "Topic\n")

    with pytest.raises(PermissionError):
        EditMemoryTool(context).apply({
            "namespace": "workspace", "filename": "notes.md",
            "old_text": "Topic", "new_text": "New topic",
        })


def test_workspace_write_permission_defaults_to_ask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, write_permission="ask")
    _write(context, "workspace", "notes.md", "Topic\n")

    with pytest.raises(PermissionAskRequired):
        EditMemoryTool(context).apply({
            "namespace": "workspace", "filename": "notes.md",
            "old_text": "Topic", "new_text": "New topic",
        })


def test_name_and_parameters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = EditMemoryTool(_context(tmp_path, monkeypatch))
    parameters = tool.parameters()

    assert tool.name() == "EditMemory"
    assert set(parameters["required"]) == {"namespace", "filename", "new_text"}
    assert {"old_text", "old_text_start", "old_text_end"} <= set(parameters["properties"])


def test_old_text_start_end_form_is_wired_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Smoke test that old_text_start/old_text_end reaches EditMemory via the shared
    EditFileCore -- the full matrix is exercised against EditFile in test_edit_file.py."""
    context = _context(tmp_path, monkeypatch)
    path = _write(context, "global", "notes.md", "Topic\nb\nc\nd\n")

    result = EditMemoryTool(context).apply({
        "namespace": "global", "filename": "notes.md",
        "old_text_start": "b", "old_text_end": "c", "new_text": "B\nC",
    })

    assert path.read_text() == "Topic\nB\nC\nd\n"
    assert result["replaced_lines"] == 2


def test_summary_reports_a_line_diff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "notes.md", "Topic\nb\nc\nd\n")
    tool = EditMemoryTool(context)
    args = {
        "namespace": "global", "filename": "notes.md",
        "old_text": "b\nc", "new_text": "B\nC\nD",
    }

    result = tool.apply(args)

    assert tool.summary(args, result) == "Edit memory: global/notes.md (+3/-2)"


def test_summary_on_failure_omits_the_diff_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = EditMemoryTool(_context(tmp_path, monkeypatch))
    args = {"namespace": "global", "filename": "notes.md", "old_text": "a", "new_text": "b"}

    assert tool.summary(args, error="not found") == "Edit memory: global/notes.md failed: not found"


def test_diff_preview_reflects_the_applied_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "notes.md", "Topic\nb\nc\n")
    tool = EditMemoryTool(context)
    args = {"namespace": "global", "filename": "notes.md", "old_text": "b", "new_text": "B"}

    result = tool.apply(args)
    preview = tool.diff_preview(args, result)

    assert preview is not None
    assert preview.label == tool.summary(args, result)
    kinds = [line.kind for hunk in preview.hunks for line in hunk.lines]
    assert kinds == ["context", "del", "add", "context"]
