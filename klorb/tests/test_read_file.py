# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.read_file."""

import json
from pathlib import Path

import pytest

from klorb.permissions.directory_access import DirRules
from klorb.permissions.table import PermissionAskRequired
from klorb.process_config import DEFAULT_READ_FILE_MAX_LINES
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.read_file import ReadFileTool
from klorb.tools.setup_context import ToolSetupContext


def _context(
    workspace_root: Path,
    *,
    max_lines: int = DEFAULT_READ_FILE_MAX_LINES,
    is_workspace_trusted: bool = False,
    read_dirs: DirRules | None = None,
    write_dirs: DirRules | None = None,
) -> ToolSetupContext:
    return ToolSetupContext(
        process_config=ProcessConfig(
            read_file_max_lines=max_lines, is_workspace_trusted=is_workspace_trusted),
        session_config=SessionConfig(
            workspace_root=workspace_root,
            read_dirs=read_dirs or DirRules(),
            write_dirs=write_dirs or DirRules(),
        ),
    )


def _write_lines(path: Path, count: int) -> Path:
    file_path = path / "sample.txt"
    file_path.write_text("\n".join(f"line {i}" for i in range(1, count + 1)) + "\n")
    return file_path


def test_reads_whole_short_file_by_default(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 3)

    result = ReadFileTool(_context(tmp_path)).apply({"filename": str(file_path)})

    assert result["start_line"] == 1
    assert result["end_line"] == 3
    assert result["total_lines"] == 3
    assert result["truncated"] is False
    assert result["content"] == "1|line 1\n2|line 2\n3|line 3"


def test_start_line_zero_means_start_at_beginning(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 3)

    result = ReadFileTool(_context(tmp_path)).apply({"filename": str(file_path), "start_line": 0})

    assert result["start_line"] == 1
    assert result["content"].startswith("1|line 1")


def test_explicit_range_returns_requested_lines(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 50)

    result = ReadFileTool(_context(tmp_path)).apply(
        {"filename": str(file_path), "start_line": 5, "end_line": 16})

    assert result["start_line"] == 5
    assert result["end_line"] == 16
    assert result["content"].splitlines() == [f"{i}|line {i}" for i in range(5, 17)]


def test_request_beyond_max_lines_is_capped(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 1000)

    result = ReadFileTool(_context(tmp_path)).apply(
        {"filename": str(file_path), "start_line": 1, "end_line": 500})

    assert result["end_line"] == DEFAULT_READ_FILE_MAX_LINES
    assert len(result["content"].splitlines()) == DEFAULT_READ_FILE_MAX_LINES
    assert result["truncated"] is True


def test_request_within_max_lines_returns_exact_count(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 50)

    result = ReadFileTool(_context(tmp_path)).apply(
        {"filename": str(file_path), "start_line": 1, "end_line": 12})

    assert len(result["content"].splitlines()) == 12
    assert result["truncated"] is True  # file has more lines beyond what was returned


def test_negative_start_line_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="start_line must be >= 0"):
        ReadFileTool(_context(tmp_path)).apply({"filename": "irrelevant.txt", "start_line": -1})


def test_end_line_before_start_line_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="end_line .* must be >= start_line"):
        ReadFileTool(_context(tmp_path)).apply(
            {"filename": "irrelevant.txt", "start_line": 10, "end_line": 5})


def test_name_and_parameters(tmp_path: Path) -> None:
    tool = ReadFileTool(_context(tmp_path))

    assert tool.name() == "ReadFile"
    assert tool.parameters()["required"] == ["filename"]


def test_custom_max_lines_caps_request(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 50)

    result = ReadFileTool(_context(tmp_path, max_lines=5)).apply(
        {"filename": str(file_path), "start_line": 1, "end_line": 50})

    assert result["end_line"] == 5
    assert len(result["content"].splitlines()) == 5
    assert result["truncated"] is True


def test_custom_max_lines_reflected_in_description(tmp_path: Path) -> None:
    tool = ReadFileTool(_context(tmp_path, max_lines=5))

    assert "up to 5" in tool.description()


# --- Permission-table integration (see docs/specs/permissions.md) ---


def test_zero_config_still_works_inside_workspace(tmp_path: Path) -> None:
    """With no readDirs/writeDirs configured, ReadFile can read any file inside
    workspace_root."""
    file_path = _write_lines(tmp_path, 3)

    result = ReadFileTool(_context(tmp_path)).apply({"filename": str(file_path)})

    assert result["total_lines"] == 3


def test_untrusted_zero_config_denies_outside_workspace(tmp_path: Path) -> None:
    """By default (untrusted workspace), ReadFile is hard-confined to workspace_root, exactly
    like the write tools."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")

    with pytest.raises(PermissionError):
        ReadFileTool(_context(workspace)).apply({"filename": str(outside)})


def test_untrusted_readdirs_allow_outside_workspace_does_not_bypass_hard_boundary(
    tmp_path: Path,
) -> None:
    """Untrusted workspaces get the same hard boundary the write tools have — a readDirs.allow
    entry outside workspace_root cannot widen past it."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")

    with pytest.raises(PermissionError):
        ReadFileTool(_context(workspace, read_dirs=DirRules(allow=[tmp_path]))).apply(
            {"filename": str(outside)})


def test_readdirs_deny_rejects_in_workspace_file(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 3)

    with pytest.raises(PermissionError):
        ReadFileTool(_context(tmp_path, read_dirs=DirRules(deny=[tmp_path]))).apply(
            {"filename": str(file_path)})


def test_readdirs_ask_raises_permission_ask_required(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 3)

    with pytest.raises(PermissionAskRequired):
        ReadFileTool(_context(tmp_path, read_dirs=DirRules(ask=[tmp_path]))).apply(
            {"filename": str(file_path)})


def test_writedirs_never_affects_read(tmp_path: Path) -> None:
    """writeDirs does not participate in a read verdict at all: a writeDirs.deny does not block
    a read, and a writeDirs.allow does not grant one, regardless of readDirs."""
    file_path = _write_lines(tmp_path, 3)

    denied_by_write = ReadFileTool(_context(tmp_path, write_dirs=DirRules(deny=[tmp_path]))).apply(
        {"filename": str(file_path)})
    assert denied_by_write["total_lines"] == 3

    with pytest.raises(PermissionError):
        ReadFileTool(_context(
            tmp_path, read_dirs=DirRules(deny=[tmp_path]), write_dirs=DirRules(allow=[tmp_path]),
        )).apply({"filename": str(file_path)})


def test_readdirs_allow_wins_over_matching_writedirs_deny(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 3)

    result = ReadFileTool(_context(
        tmp_path, read_dirs=DirRules(allow=[tmp_path]), write_dirs=DirRules(deny=[tmp_path]),
    )).apply({"filename": str(file_path)})

    assert result["total_lines"] == 3


def test_trusted_readdirs_allow_reaches_outside_workspace(tmp_path: Path) -> None:
    """A trusted workspace's readDirs.allow can grant access outside workspace_root — not
    reachable via any code path outside tests today, since nothing sets
    ProcessConfig.is_workspace_trusted True in production code."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("line 1\n")

    result = ReadFileTool(_context(
        workspace, is_workspace_trusted=True, read_dirs=DirRules(allow=[tmp_path]),
    )).apply({"filename": str(outside)})

    assert result["total_lines"] == 1


def test_trusted_empty_tables_deny_everything(tmp_path: Path) -> None:
    """No magic "inside the workspace" fallback in trusted mode: default grants are meant to
    come from an explicit readDirs.allow entry a future project-bootstrap flow writes, not from
    a rule baked into the evaluator."""
    file_path = _write_lines(tmp_path, 3)

    with pytest.raises(PermissionError):
        ReadFileTool(_context(tmp_path, is_workspace_trusted=True)).apply(
            {"filename": str(file_path)})


# --- summary()/detail_view() (see docs/specs/terminal-repl.md) ---


def test_summary_on_success_names_the_file_and_line_range(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 50)
    tool = ReadFileTool(_context(tmp_path))
    result = tool.apply({"filename": str(file_path), "start_line": 5, "end_line": 16})

    assert tool.summary({"filename": str(file_path)}, result) == (
        f"Read file: {file_path} (lines 5-16 of 50)")


def test_summary_on_failure_includes_the_error() -> None:
    tool = ReadFileTool(_context(Path("/tmp")))

    assert tool.summary({"filename": "missing.txt"}, error="not found") == (
        "Read file: missing.txt failed: not found")


def test_detail_view_truncates_long_content_to_eight_lines(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 50)
    tool = ReadFileTool(_context(tmp_path))
    result = tool.apply({"filename": str(file_path)})

    detail = json.loads(tool.detail_view({"filename": str(file_path)}, result))

    assert detail["result"]["content"] == "\n".join(f"{i}|line {i}" for i in range(1, 9)) + "\n..."
    assert detail["result"]["total_lines"] == 50


def test_detail_view_leaves_short_content_untouched(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 3)
    tool = ReadFileTool(_context(tmp_path))
    result = tool.apply({"filename": str(file_path)})

    detail = json.loads(tool.detail_view({"filename": str(file_path)}, result))

    assert detail["result"]["content"] == result["content"]
