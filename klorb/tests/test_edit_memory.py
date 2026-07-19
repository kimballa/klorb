# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.memory.edit_memory."""

from pathlib import Path

import pytest

from klorb.permissions.table import PermissionAskRequired, Verdict
from klorb.process_config import DEFAULT_EDIT_FILE_DRIFT_SEARCH_RADIUS, ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.memory import common as memory_common_module
from klorb.tools.memory.common import Namespace, memory_namespace_dir
from klorb.tools.memory.edit_memory import EditMemoryTool
from klorb.tools.setup_context import ToolSetupContext
from klorb.workspace import Workspace


def _context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *,
    trusted: bool = True, edit_permission: Verdict = "allow",
    drift_search_radius: int = DEFAULT_EDIT_FILE_DRIFT_SEARCH_RADIUS,
) -> ToolSetupContext:
    monkeypatch.setattr(memory_common_module, "KLORB_DATA_DIR", tmp_path / "data")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    return ToolSetupContext(
        process_config=ProcessConfig(
            memory_edit_permission=edit_permission,
            edit_file_drift_search_radius=drift_search_radius),
        session_config=SessionConfig(workspace=Workspace(path=workspace_root, trusted=trusted)))


def _write(context: ToolSetupContext, namespace: Namespace, filename: str, content: str) -> Path:
    namespace_dir = memory_namespace_dir(context, namespace)
    namespace_dir.mkdir(parents=True, exist_ok=True)
    path = namespace_dir / filename
    path.write_text(content)
    return path


def test_replaces_a_line_range(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    path = _write(context, "global", "notes.md", "Topic\nb\nc\nd\n")

    result = EditMemoryTool(context).apply({
        "namespace": "global", "filename": "notes.md",
        "start_line": 2, "end_line": 3, "start_text": "b", "end_text": "c", "new_text": "B\nC",
    })

    assert path.read_text() == "Topic\nB\nC\nd\n"
    assert result["namespace"] == "global"
    assert result["filename"] == "notes.md"


def test_drift_relocates_a_single_line_edit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    path = _write(context, "global", "notes.md", "Topic\np\nq\nr\ns\n")

    result = EditMemoryTool(context).apply({
        "namespace": "global", "filename": "notes.md",
        "start_line": 2, "end_line": 2, "start_text": "q", "end_text": "q", "new_text": "Q",
    })

    assert path.read_text() == "Topic\np\nQ\nr\ns\n"
    assert result["line_hint_matched"] is False


def test_drift_verification_failure_names_read_memory_reread_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, drift_search_radius=2)
    _write(context, "global", "notes.md", "Topic\n" + "\n".join(f"L{i}" for i in range(1, 11)) + "\n")

    with pytest.raises(ValueError, match="re-ReadMemory global/notes.md"):
        EditMemoryTool(context).apply({
            "namespace": "global", "filename": "notes.md",
            "start_line": 1, "end_line": 1, "start_text": "L5", "end_text": "L5", "new_text": "FIVE",
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
            "start_line": 1, "end_line": 1, "start_text": "Topic", "end_text": "Topic",
            "new_text": "   ",
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
            "start_line": 1, "end_line": 1, "start_text": "Topic", "end_text": "Topic",
            "new_text": "",
        })

    assert path.read_text() == "Topic\n\nbody\n"


def test_edit_leaving_a_non_blank_first_line_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    path = _write(context, "global", "notes.md", "Topic\nbody\n")

    EditMemoryTool(context).apply({
        "namespace": "global", "filename": "notes.md",
        "start_line": 1, "end_line": 1, "start_text": "Topic", "end_text": "Topic",
        "new_text": "New topic",
    })

    assert path.read_text() == "New topic\nbody\n"


def test_nonexistent_filename_raises_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shape other than the empty-subject insert can't auto-create a memory (see
    test_edit_memory_auto_creates_nonexistent_memory_via_empty_insert_shape); it fails and
    names CreateMemory as the tool to use instead."""
    context = _context(tmp_path, monkeypatch)

    with pytest.raises(FileNotFoundError, match="does not exist; use CreateMemory"):
        EditMemoryTool(context).apply({
            "namespace": "global", "filename": "missing.md",
            "start_line": 1, "end_line": 1, "start_text": "a", "end_text": "a", "new_text": "b",
        })


def test_edit_memory_auto_creates_nonexistent_memory_via_empty_insert_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A namespace/filename pair with nothing on disk (not even the namespace directory) is
    treated exactly like an existing-but-empty memory for the empty-subject insert shape -- no
    prior CreateMemory call needed."""
    context = _context(tmp_path, monkeypatch)
    path = memory_namespace_dir(context, "global") / "notes.md"
    assert not path.exists()

    result = EditMemoryTool(context).apply({
        "namespace": "global", "filename": "notes.md",
        "start_line": 1, "end_line": 0, "start_text": "", "end_text": "",
        "new_text": "Topic\nBody",
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
            "start_line": 1, "end_line": 0, "start_text": "", "end_text": "",
            "new_text": "   ",
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
            "start_line": 2, "end_line": 2, "start_text": "body", "end_text": "body",
            "new_text": "new body",
        })

    assert path.read_text() == "Topic\nbody\n"


def test_edit_permission_deny_raises_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, edit_permission="deny")
    _write(context, "global", "notes.md", "Topic\n")

    with pytest.raises(PermissionError):
        EditMemoryTool(context).apply({
            "namespace": "global", "filename": "notes.md",
            "start_line": 1, "end_line": 1, "start_text": "Topic", "end_text": "Topic",
            "new_text": "New topic",
        })


def test_edit_permission_ask_raises_permission_ask_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, edit_permission="ask")
    _write(context, "global", "notes.md", "Topic\n")

    with pytest.raises(PermissionAskRequired):
        EditMemoryTool(context).apply({
            "namespace": "global", "filename": "notes.md",
            "start_line": 1, "end_line": 1, "start_text": "Topic", "end_text": "Topic",
            "new_text": "New topic",
        })


def test_name_and_parameters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = EditMemoryTool(_context(tmp_path, monkeypatch))
    parameters = tool.parameters()

    assert tool.name() == "EditMemory"
    assert set(parameters["required"]) == {"namespace", "filename", "new_text"}
    assert "old_text" in parameters["properties"]
    assert "start_line" not in parameters["required"]


def test_old_text_form_is_wired_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke test that the widened argument matrix (see docs/specs/tool-framework.md)
    reaches EditMemory via the shared EditFileCore -- the full matrix is exercised against
    EditFile in test_edit_file.py."""
    context = _context(tmp_path, monkeypatch)
    path = _write(context, "global", "notes.md", "Topic\nb\nc\nd\n")

    result = EditMemoryTool(context).apply({
        "namespace": "global", "filename": "notes.md",
        "start_line": 2, "old_text": "b\nc", "new_text": "B\nC",
    })

    assert path.read_text() == "Topic\nB\nC\nd\n"
    assert result["requested_end_line"] == 3


def test_old_text_with_no_line_hint_is_wired_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Smoke test that old_text's whole-subject search (no line hint at all) reaches
    EditMemory via the shared EditFileCore -- the full matrix is exercised against EditFile
    in test_edit_file.py."""
    context = _context(tmp_path, monkeypatch)
    path = _write(context, "global", "notes.md", "Topic\nb\nc\nd\n")

    result = EditMemoryTool(context).apply({
        "namespace": "global", "filename": "notes.md",
        "old_text": "b\nc", "new_text": "B\nC",
    })

    assert path.read_text() == "Topic\nB\nC\nd\n"
    assert result["requested_start_line"] == 1


def test_summary_reports_a_line_diff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "notes.md", "Topic\nb\nc\nd\n")
    tool = EditMemoryTool(context)
    args = {
        "namespace": "global", "filename": "notes.md",
        "start_line": 2, "end_line": 3, "start_text": "b", "end_text": "c", "new_text": "B\nC\nD",
    }

    result = tool.apply(args)

    assert tool.summary(args, result) == "Edit memory: global/notes.md (+3/-2)"


def test_summary_on_failure_includes_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = EditMemoryTool(_context(tmp_path, monkeypatch))
    args = {
        "namespace": "global", "filename": "notes.md",
        "start_line": 1, "end_line": 1, "start_text": "a", "end_text": "a", "new_text": "b",
    }

    assert tool.summary(args, error="not found") == "Edit memory: global/notes.md (+1/-1) failed: not found"
