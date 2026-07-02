# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.edit_file."""

from pathlib import Path

import pytest

from klorb.permissions.directory_access import DirRules
from klorb.permissions.table import PermissionAskRequired
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.edit_file import EditFileTool
from klorb.tools.setup_context import ToolSetupContext


def _context(
    workspace_root: Path, *, read_dirs: DirRules | None = None, write_dirs: DirRules | None = None,
) -> ToolSetupContext:
    """Defaults both `readDirs`/`writeDirs` to allowing all of `workspace_root`, since
    `evaluate_write()` now requires an explicit allow in *both* tables (see
    docs/adrs/write-verdict-is-stricter-of-read-and-write-tables.md) and most tests here are
    about EditFile's own logic, not the permission system -- only the "Permission-table
    integration" tests below pass an explicit override to narrow that default."""
    return ToolSetupContext(
        process_config=ProcessConfig(),
        session_config=SessionConfig(
            workspace_root=workspace_root,
            read_dirs=read_dirs or DirRules(allow=[workspace_root]),
            write_dirs=write_dirs or DirRules(allow=[workspace_root])))


def _write(path: Path, name: str, content: str) -> Path:
    file_path = path / name
    file_path.write_text(content)
    return file_path


def test_replaces_a_line_range(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\nd\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 2, "end_line": 3,
        "start_text": "b", "end_text": "c", "new_text": "B\nC",
    })

    assert file_path.read_text() == "a\nB\nC\nd\n"
    assert result["new_total_lines"] == 4
    assert result["content"] == "2|B\n3|C"


def test_replaces_starting_at_line_one(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 1, "end_line": 1,
        "start_text": "a", "end_text": "a", "new_text": "A",
    })

    assert file_path.read_text() == "A\nb\nc\n"


def test_insert_via_single_line_replace(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 2, "end_line": 2,
        "start_text": "b", "end_text": "b", "new_text": "x\nb",
    })

    assert file_path.read_text() == "a\nx\nb\nc\n"


def test_delete_via_empty_new_text(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 2, "end_line": 2,
        "start_text": "b", "end_text": "b", "new_text": "",
    })

    assert file_path.read_text() == "a\nc\n"
    assert result["new_total_lines"] == 2


def test_delete_all_lines_leaves_empty_file(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 1, "end_line": 3,
        "start_text": "a", "end_text": "c", "new_text": "",
    })

    assert file_path.read_text() == ""


def test_insert_into_empty_file(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 1, "end_line": 0,
        "start_text": "", "end_text": "", "new_text": "hello\nworld",
    })

    assert file_path.read_text() == "hello\nworld\n"
    assert result["new_total_lines"] == 2


def test_insert_into_empty_file_rejects_nonzero_end_line(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "")

    with pytest.raises(ValueError, match="is empty"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 1,
            "start_text": "", "end_text": "", "new_text": "x",
        })


def test_whole_file_replacement(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 1, "end_line": 3,
        "start_text": "a", "end_text": "c", "new_text": "x\ny",
    })

    assert file_path.read_text() == "x\ny\n"


def test_edit_touching_eof_always_terminates_with_newline_regardless_of_original(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc")  # no trailing newline

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 3, "end_line": 3,
        "start_text": "c", "end_text": "c", "new_text": "z",
    })

    assert file_path.read_text() == "a\nb\nz\n"


def test_edit_touching_eof_ignores_trailing_newline_in_new_text(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 3, "end_line": 3,
        "start_text": "c", "end_text": "c", "new_text": "z\n",
    })

    assert file_path.read_text() == "a\nb\nz\n"


def test_edit_not_touching_eof_preserves_original_trailing_newline_absent(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc")  # no trailing newline

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 1, "end_line": 1,
        "start_text": "a", "end_text": "a", "new_text": "A",
    })

    assert file_path.read_text() == "A\nb\nc"


def test_edit_not_touching_eof_preserves_original_trailing_newline_present(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 1, "end_line": 1,
        "start_text": "a", "end_text": "a", "new_text": "A",
    })

    assert file_path.read_text() == "A\nb\nc\n"


def test_drift_verification_failure_on_start_text(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="start_text does not match"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 1,
            "start_text": "STALE", "end_text": "a", "new_text": "A",
        })


def test_drift_verification_failure_on_end_text(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="end_text does not match"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 2,
            "start_text": "a", "end_text": "STALE", "new_text": "A",
        })


def test_out_of_bounds_line_numbers_raise(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="out of range"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 10,
            "start_text": "a", "end_text": "c", "new_text": "x",
        })


def test_end_line_before_start_line_raises(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="out of range"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 3, "end_line": 1,
            "start_text": "c", "end_text": "a", "new_text": "x",
        })


def test_non_integer_line_number_raises(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="start_line must be an integer"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": "1", "end_line": 1,
            "start_text": "a", "end_text": "a", "new_text": "x",
        })


def test_boolean_line_number_raises(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="end_line must be an integer"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 1, "end_line": True,
            "start_text": "a", "end_text": "a", "new_text": "x",
        })


def test_path_outside_workspace_root_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("a\n")

    with pytest.raises(PermissionError):
        EditFileTool(_context(workspace)).apply({
            "filename": str(outside), "start_line": 1, "end_line": 1,
            "start_text": "a", "end_text": "a", "new_text": "x",
        })


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("a\n")
    link = workspace / "link.txt"
    link.symlink_to(outside)

    with pytest.raises(PermissionError):
        EditFileTool(_context(workspace)).apply({
            "filename": str(link), "start_line": 1, "end_line": 1,
            "start_text": "a", "end_text": "a", "new_text": "x",
        })


def test_name_and_parameters(tmp_path: Path) -> None:
    tool = EditFileTool(_context(tmp_path))

    assert tool.name() == "EditFile"
    assert set(tool.parameters()["required"]) == {
        "filename", "start_line", "end_line", "start_text", "end_text", "new_text"}


# --- Permission-table integration (see docs/specs/permissions.md) ---


def test_writedirs_deny_rejects_an_otherwise_in_workspace_edit(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(PermissionError):
        EditFileTool(_context(tmp_path, write_dirs=DirRules(deny=[tmp_path]))).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 1,
            "start_text": "a", "end_text": "a", "new_text": "A",
        })

    assert file_path.read_text() == "a\nb\nc\n"


def test_writedirs_ask_raises_permission_ask_required(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(PermissionAskRequired):
        EditFileTool(_context(tmp_path, write_dirs=DirRules(ask=[tmp_path]))).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 1,
            "start_text": "a", "end_text": "a", "new_text": "A",
        })

    assert file_path.read_text() == "a\nb\nc\n"


def test_hard_workspace_boundary_wins_even_if_writedirs_allow_covers_outside(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("a\n")

    with pytest.raises(PermissionError):
        EditFileTool(_context(workspace, write_dirs=DirRules(allow=[tmp_path]))).apply({
            "filename": str(outside), "start_line": 1, "end_line": 1,
            "start_text": "a", "end_text": "a", "new_text": "x",
        })

    assert outside.read_text() == "a\n"


def test_klorb_dir_write_implicitly_denied_even_with_no_config(tmp_path: Path) -> None:
    klorb_dir = tmp_path / ".klorb"
    klorb_dir.mkdir()
    file_path = klorb_dir / "klorb-config.json"
    file_path.write_text("{}")

    with pytest.raises(PermissionError):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 1,
            "start_text": "{}", "end_text": "{}", "new_text": "{\"tampered\": true}",
        })

    assert file_path.read_text() == "{}"
