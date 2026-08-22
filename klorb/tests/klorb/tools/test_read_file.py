# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.read_file."""

import json
from pathlib import Path

import pytest

from klorb.permissions.directory_access import DirRules
from klorb.permissions.table import PermissionAskRequired
from klorb.process_config import DEFAULT_READ_FILE_MAX_LINE_LENGTH, DEFAULT_READ_FILE_MAX_LINES, ProcessConfig
from klorb.session import Session, SessionConfig, WorkspaceAccess
from klorb.tools.read_file import ReadFileTool
from klorb.tools.setup_context import ToolSetupContext
from klorb.workspace import Workspace

_AWS_KEY = "AKIAABCDEFGHIJKLMNOP"


def _context(
    workspace_root: Path,
    *,
    max_lines: int = DEFAULT_READ_FILE_MAX_LINES,
    max_line_length: int = DEFAULT_READ_FILE_MAX_LINE_LENGTH,
    is_workspace_trusted: bool = False,
    read_dirs: DirRules | None = None,
    write_dirs: DirRules | None = None,
    session: Session | None = None,
) -> ToolSetupContext:
    return ToolSetupContext(
        process_config=ProcessConfig(
            read_file_max_lines=max_lines,
            read_file_max_line_length=max_line_length,
        ),
        session_config=SessionConfig(workspace_access=WorkspaceAccess(
            workspace=Workspace(path=workspace_root, trusted=is_workspace_trusted),
            read_dirs=read_dirs or DirRules(),
            write_dirs=write_dirs or DirRules(),
        )),
        session=session,
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
    assert result["next_start_line"] == 13


def test_truncated_response_includes_next_start_line(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 1000)

    result = ReadFileTool(_context(tmp_path)).apply(
        {"filename": str(file_path), "start_line": 1, "end_line": 500})

    assert result["truncated"] is True
    assert result["next_start_line"] == result["end_line"] + 1
    assert result["next_start_line"] == DEFAULT_READ_FILE_MAX_LINES + 1


def test_untruncated_response_omits_next_start_line(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 3)

    result = ReadFileTool(_context(tmp_path)).apply({"filename": str(file_path)})

    assert result["truncated"] is False
    assert "next_start_line" not in result


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
    """A trusted workspace's readDirs.allow can grant access outside workspace_root — set in
    production via the interactive workspace-trust flow (see docs/specs/projects-and-trust.md),
    constructed directly here via ProcessConfig(is_workspace_trusted=True) instead."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("line 1\n")

    result = ReadFileTool(_context(
        workspace, is_workspace_trusted=True, read_dirs=DirRules(allow=[tmp_path]),
    )).apply({"filename": str(outside)})

    assert result["total_lines"] == 1


def test_trusted_empty_tables_ask_about_everything(tmp_path: Path) -> None:
    """No magic "inside the workspace" fallback in trusted mode: default *allow* grants are
    meant to come from an explicit readDirs.allow entry a project-bootstrap flow writes, not
    from a rule baked into the evaluator -- but an unmentioned path still reaches the
    interactive-ask flow rather than being refused outright."""
    file_path = _write_lines(tmp_path, 3)

    with pytest.raises(PermissionAskRequired):
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


# --- read_preview() (see docs/specs/terminal-repl.md) ---


def test_read_preview_caps_to_four_lines_and_flags_truncation(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 10)
    tool = ReadFileTool(_context(tmp_path))
    args = {"filename": str(file_path)}
    result = tool.apply(args)

    preview = tool.read_preview(args, result)

    assert preview is not None
    assert preview.label == tool.summary(args, result)
    assert preview.preview_lines == [(1, "line 1"), (2, "line 2"), (3, "line 3"), (4, "line 4")]
    assert preview.truncated is True


def test_read_preview_not_truncated_when_range_fits(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 3)
    tool = ReadFileTool(_context(tmp_path))
    args = {"filename": str(file_path)}
    result = tool.apply(args)

    preview = tool.read_preview(args, result)

    assert preview is not None
    assert preview.truncated is False


def test_read_preview_is_none_on_failure(tmp_path: Path) -> None:
    tool = ReadFileTool(_context(tmp_path))

    assert tool.read_preview({"filename": "missing.txt"}, None, "no such file") is None


def test_read_preview_open_full_rereads_the_whole_file(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 10)
    tool = ReadFileTool(_context(tmp_path))
    args = {"filename": str(file_path), "start_line": 3, "end_line": 4}
    result = tool.apply(args)

    preview = tool.read_preview(args, result)
    assert preview is not None
    full_view = preview.open_full()

    assert full_view.error is None
    assert full_view.scroll_to_line == 3
    assert full_view.lines is not None
    assert len(full_view.lines) == 10
    assert full_view.lines[0] == (1, "line 1")


def test_read_preview_open_full_reports_an_error_if_the_file_is_gone(tmp_path: Path) -> None:
    file_path = _write_lines(tmp_path, 3)
    tool = ReadFileTool(_context(tmp_path))
    args = {"filename": str(file_path)}
    result = tool.apply(args)
    preview = tool.read_preview(args, result)
    assert preview is not None

    file_path.unlink()
    full_view = preview.open_full()

    assert full_view.lines is None
    assert full_view.error is not None


# --- Line wrapping (maxLineLength) ---


def test_long_line_is_wrapped(tmp_path: Path) -> None:
    """A line longer than max_line_length is wrapped into multiple output lines."""
    file_path = tmp_path / "long.txt"
    file_path.write_text("a" * 20 + "\n")
    result = ReadFileTool(_context(tmp_path, max_line_length=10)).apply(
        {"filename": str(file_path)})

    lines = result["content"].splitlines()
    assert len(lines) == 2
    assert lines[0] == "1|" + "a" * 10
    assert lines[1] == "1|" + "a" * 10


def test_short_line_is_not_wrapped(tmp_path: Path) -> None:
    """A line that fits within max_line_length is output as-is."""
    file_path = tmp_path / "short.txt"
    file_path.write_text("hello\n")
    result = ReadFileTool(_context(tmp_path, max_line_length=100)).apply(
        {"filename": str(file_path)})

    assert result["content"] == "1|hello"


def test_wrapped_lines_count_toward_max_lines(tmp_path: Path) -> None:
    """Wrapped continuation lines count toward the max_lines budget."""
    file_path = tmp_path / "wrap_count.txt"
    # Each line is 30 chars; with max_line_length=10, each wraps to 3 output lines.
    # With max_lines=7, we can fit 2 full wrapped lines (6 output lines) plus 1 segment
    # of the 3rd wrapped line.
    file_path.write_text("\n".join("x" * 30 for _ in range(5)) + "\n")
    result = ReadFileTool(_context(tmp_path, max_lines=7, max_line_length=10)).apply(
        {"filename": str(file_path)})

    lines = result["content"].splitlines()
    assert len(lines) == 7
    assert result["truncated"] is True


def test_truncated_mid_line_sets_same_start_line(tmp_path: Path) -> None:
    """When truncation happens mid-wrapped-line, next_start_line equals the current line."""
    file_path = tmp_path / "trunc.txt"
    file_path.write_text("a" * 50 + "\n" + "b" * 50 + "\n")
    # max_lines=3, max_line_length=20 → line 1 wraps to 3 segments (50/20=2.5→3).
    # All 3 slots are consumed by line 1; line 2 can't start → truncation mid-line.
    result = ReadFileTool(_context(tmp_path, max_lines=3, max_line_length=20)).apply(
        {"filename": str(file_path)})

    assert result["truncated"] is True
    assert result["next_start_line"] == 2  # line 2 was the one being wrapped when budget ran out
    assert "truncation_cause" in result
    assert "mid-line" in result["truncation_cause"]


def test_insanely_long_line_suggests_wrap_width(tmp_path: Path) -> None:
    """When the first line alone exceeds the budget, truncation_cause suggests wrap_width."""
    file_path = tmp_path / "insane.txt"
    file_path.write_text("z" * 1000 + "\nshort\n")
    # max_lines=5, max_line_length=100 → line 1 needs 10 segments, exceeds budget.
    result = ReadFileTool(_context(tmp_path, max_lines=5, max_line_length=100)).apply(
        {"filename": str(file_path)})

    assert result["truncated"] is True
    assert "truncation_cause" in result
    assert "wrap_width=200" in result["truncation_cause"]
    assert "start_line=2" in result["truncation_cause"]


def test_wrap_width_override(tmp_path: Path) -> None:
    """The unlisted wrap_width arg overrides the default max_line_length."""
    file_path = tmp_path / "override.txt"
    file_path.write_text("a" * 100 + "\n")
    # max_line_length=100 (default) would produce 1 output line.
    # wrap_width=30 should produce 4 segments (100/30=3.33→4).
    result = ReadFileTool(_context(tmp_path, max_line_length=100)).apply(
        {"filename": str(file_path), "wrap_width": 30})

    lines = result["content"].splitlines()
    assert len(lines) == 4
    assert lines[0] == "1|" + "a" * 30
    assert lines[3] == "1|" + "a" * 10


def test_wrap_width_string_is_parsed(tmp_path: Path) -> None:
    """A string wrap_width that can be parsed as int is accepted."""
    file_path = tmp_path / "strwidth.txt"
    file_path.write_text("x" * 50 + "\n")
    result = ReadFileTool(_context(tmp_path, max_line_length=100)).apply(
        {"filename": str(file_path), "wrap_width": "20"})

    lines = result["content"].splitlines()
    assert len(lines) == 3  # 50/20=2.5→3


def test_wrap_width_zero_or_negative_ignored(tmp_path: Path) -> None:
    """A zero or negative wrap_width is ignored; the default is used."""
    file_path = tmp_path / "badwidth.txt"
    file_path.write_text("a" * 50 + "\n")
    result = ReadFileTool(_context(tmp_path, max_line_length=100)).apply(
        {"filename": str(file_path), "wrap_width": 0})

    # 50 chars fits in 100-char limit, so 1 output line.
    assert len(result["content"].splitlines()) == 1


def test_truncation_at_line_boundary_omits_cause(tmp_path: Path) -> None:
    """When truncation happens at a line boundary (not mid-wrap), no truncation_cause."""
    file_path = _write_lines(tmp_path, 10)
    result = ReadFileTool(_context(tmp_path, max_lines=3)).apply(
        {"filename": str(file_path)})

    assert result["truncated"] is True
    assert "truncation_cause" not in result
    assert result["next_start_line"] == 4


# --- Secret redaction (see docs/specs/secret-redaction.md) ---


def test_read_file_redacts_a_likely_credential(tmp_path: Path) -> None:
    file_path = tmp_path / "creds.env"
    file_path.write_text(f"AWS_ACCESS_KEY_ID={_AWS_KEY}\nordinary line\n")

    result = ReadFileTool(_context(tmp_path)).apply({"filename": str(file_path)})

    assert _AWS_KEY not in result["content"]
    assert "[[SECRET:" in result["content"]
    assert result["content"].splitlines()[1] == "2|ordinary line"


def test_read_file_total_lines_unaffected_by_redaction(tmp_path: Path) -> None:
    """Paging metadata reflects the real file, not the redacted content."""
    file_path = tmp_path / "creds.env"
    file_path.write_text(f"AWS_ACCESS_KEY_ID={_AWS_KEY}\n")

    result = ReadFileTool(_context(tmp_path)).apply({"filename": str(file_path)})

    assert result["total_lines"] == 1
    assert result["truncated"] is False
