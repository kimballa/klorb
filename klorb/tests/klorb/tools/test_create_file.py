# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.create_file."""
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from klorb.permissions.directory_access import DirRules
from klorb.permissions.table import PermissionAskRequired
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig, WorkspaceAccess
from klorb.tools.create_file import CreateFileTool
from klorb.tools.read_file import ReadFileTool
from klorb.tools.response_envelope import ToolCallErrorInfo
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.util import CreateFileCore
from klorb.workspace import Workspace


def _context(
    workspace_root: Path, *, read_dirs: DirRules | None = None, write_dirs: DirRules | None = None,
    session: Session | None = None,
) -> ToolSetupContext:
    """Defaults both `readDirs`/`writeDirs` to allowing all of `workspace_root`, since
    `evaluate_write()` requires an explicit allow in *both* tables (see
    docs/adrs/00030-write-verdict-is-stricter-of-read-and-write-tables.md) and most tests here are
    about CreateFile's own logic, not the permission system -- only the "Permission-table
    integration" tests below pass an explicit override to narrow that default."""
    return ToolSetupContext(
        process_config=ProcessConfig(),
        session_config=SessionConfig(workspace_access=WorkspaceAccess(
            workspace=Workspace(path=workspace_root),
            read_dirs=read_dirs or DirRules(allow=[workspace_root]),
            write_dirs=write_dirs or DirRules(allow=[workspace_root]))),
        session=session)


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
    parameters = tool.parameters()

    assert tool.name() == "CreateFile"
    assert set(parameters["required"]) == {"content"}
    assert {"filename"} <= set(parameters["properties"])


def test_creates_a_new_file_via_path(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"

    result = CreateFileTool(_context(tmp_path)).apply(
        {"path": str(file_path), "content": "a\nb\nc\n"})

    assert file_path.read_text() == "a\nb\nc\n"
    assert result["filename"] == str(file_path)


def test_filename_and_path_both_given_raises(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"

    with pytest.raises(ValueError, match="Provide either 'filename' or 'path', not both"):
        CreateFileTool(_context(tmp_path)).apply(
            {"filename": str(file_path), "path": str(file_path), "content": "x\n"})

    assert not file_path.exists()


def test_neither_filename_nor_path_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Missing required argument: 'filename' or 'path'"):
        CreateFileTool(_context(tmp_path)).apply({"content": "x\n"})


def test_delegates_file_creation_to_create_file_core(tmp_path: Path) -> None:
    tool = CreateFileTool(_context(tmp_path))
    assert isinstance(tool.create_file_core, CreateFileCore)


# --- Permission-table integration (see docs/specs/permissions.md) ---


def test_writedirs_deny_rejects_an_otherwise_in_workspace_write(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"

    with pytest.raises(PermissionError):
        CreateFileTool(_context(tmp_path, write_dirs=DirRules(deny=[tmp_path]))).apply(
            {"filename": str(file_path), "content": "x\n"})

    assert not file_path.exists()


def test_writedirs_ask_raises_permission_ask_required(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"

    with pytest.raises(PermissionAskRequired):
        CreateFileTool(_context(tmp_path, write_dirs=DirRules(ask=[tmp_path]))).apply(
            {"filename": str(file_path), "content": "x\n"})

    assert not file_path.exists()


def test_hard_workspace_boundary_wins_even_if_writedirs_allow_covers_outside(tmp_path: Path) -> None:
    """writeDirs.allow can only ever narrow access within workspace_root, never widen past the
    hard resolve_within_workspace() boundary."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"

    with pytest.raises(PermissionError):
        CreateFileTool(_context(workspace, write_dirs=DirRules(allow=[tmp_path]))).apply(
            {"filename": str(outside), "content": "x\n"})

    assert not outside.exists()


def test_writedirs_allow_alone_does_not_grant_write_without_readdirs_allow(tmp_path: Path) -> None:
    """writeDirs.allow alone does not grant write access to a path readDirs is silent on --
    write access is never more permissive than read access for the same path; see
    docs/adrs/00030-write-verdict-is-stricter-of-read-and-write-tables.md. An integration-level
    counterpart to test_permissions.py's unit-level
    test_evaluate_write_asks_when_writedirs_allows_but_readdirs_is_silent, since this file's
    other permission tests all use the (allow, allow) default context."""
    file_path = tmp_path / "new.txt"

    with pytest.raises(PermissionAskRequired):
        CreateFileTool(_context(
            tmp_path, read_dirs=DirRules(), write_dirs=DirRules(allow=[tmp_path]),
        )).apply({"filename": str(file_path), "content": "x\n"})

    assert not file_path.exists()


def test_klorb_dir_write_implicitly_denied_even_with_no_config(tmp_path: Path) -> None:
    """${workspace_root}/.klorb/ is implicitly write-denied regardless of writeDirs config —
    the agent must not be able to rewrite its own permission grants."""
    file_path = tmp_path / ".klorb" / "klorb-config.json"

    with pytest.raises(PermissionError):
        CreateFileTool(_context(tmp_path)).apply({"filename": str(file_path), "content": "{}"})

    assert not file_path.exists()


def test_klorb_dir_write_denied_even_with_writedirs_allow_covering_whole_workspace(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / ".klorb" / "klorb-config.json"

    with pytest.raises(PermissionError):
        CreateFileTool(_context(tmp_path, write_dirs=DirRules(allow=[tmp_path]))).apply(
            {"filename": str(file_path), "content": "{}"})

    assert not file_path.exists()


# --- summary() (see docs/specs/terminal-repl.md) ---


def test_summary_on_success_names_the_file_and_line_count(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"
    tool = CreateFileTool(_context(tmp_path))
    args = {"filename": str(file_path), "content": "a\nb\nc\n"}

    result = tool.apply(args)

    assert tool.summary(args, result) == f"Create file: {file_path} (3 lines)"


def test_summary_on_failure_includes_the_error() -> None:
    tool = CreateFileTool(_context(Path("/tmp")))

    assert tool.summary({"filename": "existing.txt"}, error="already exists") == (
        "Create file: existing.txt failed: already exists")


def test_diff_preview_is_none_on_success(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"
    tool = CreateFileTool(_context(tmp_path))
    args = {"filename": str(file_path), "content": "a\nb\n"}

    result = tool.apply(args)

    assert tool.diff_preview(args, result) is None


def test_diff_preview_is_none_on_failure(tmp_path: Path) -> None:
    tool = CreateFileTool(_context(tmp_path))

    assert tool.diff_preview({"filename": "existing.txt"}, None, "already exists") is None


# --- format_response() (see docs/adrs/00207-render-tool-response-wire-text-at-send-time-not-storage.md) ---


def test_apply_result_carries_a_no_readfile_verification_note(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"

    result = CreateFileTool(_context(tmp_path)).apply(
        {"filename": str(file_path), "content": "a\nb\n"})

    assert result["note"] == "No verification ReadFile needed."


def test_format_response_renders_headers_then_line_numbered_content(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"
    tool = CreateFileTool(_context(tmp_path))
    args = {"filename": str(file_path), "content": "a\nb\n"}

    result = tool.apply(args)
    rendered = tool.format_response(result)

    header, content_block = rendered.split("\n\n")
    assert header.splitlines()[0] == f"filename: {file_path}"
    assert "note: No verification ReadFile needed." in header
    assert content_block == "Created content:\n========\n1|a\n2|b"


# --- Secret redaction (see docs/specs/secret-redaction.md) ---

_AWS_KEY = "AKIAABCDEFGHIJKLMNOP"


def _session(tmp_path: Path, make_session_config: Callable[..., SessionConfig]) -> Session:
    config = make_session_config(
        workspace=Workspace(path=tmp_path), read_dirs=DirRules(allow=[tmp_path]),
        write_dirs=DirRules(allow=[tmp_path]))
    return Session(config=config)


def test_create_file_token_round_trip_preserves_the_real_secret(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """The read/create loop: a `[[SECRET:...]]` token echoed in CreateFile's content resolves
    to the real secret before writing, and the real secret -- never the literal token text --
    is what ends up on disk."""
    session = _session(tmp_path, make_session_config)
    try:
        # Write a source file containing a secret, then read it to get a token.
        src = tmp_path / "creds.env"
        src.write_text(f"AWS_ACCESS_KEY_ID={_AWS_KEY}\nfoo\n")
        read_result = ReadFileTool(_context(tmp_path, session=session)).apply(
            {"filename": str(src)})
        token_line = read_result["content"].splitlines()[0].split("|", 1)[1]
        assert _AWS_KEY not in token_line
        assert token_line.startswith("AWS_ACCESS_KEY_ID=[[SECRET:")

        # Create a new file carrying the token -- the real secret must land on disk.
        dest = tmp_path / "copy.env"
        result = CreateFileTool(_context(tmp_path, session=session)).apply(
            {"filename": str(dest), "content": f"{token_line}\nbar\n"})

        assert dest.read_text() == f"AWS_ACCESS_KEY_ID={_AWS_KEY}\nbar\n"
        # The tool result must not echo the plaintext secret.
        assert _AWS_KEY not in result["content"]
    finally:
        session.close()


def test_create_file_without_a_token_behaves_normally(tmp_path: Path) -> None:
    """A redactor is always attached, but a create that never mentions a token is unaffected."""
    file_path = tmp_path / "new.txt"

    result = CreateFileTool(_context(tmp_path)).apply(
        {"filename": str(file_path), "content": "a\nb\nc\n"})

    assert file_path.read_text() == "a\nb\nc\n"
    assert result["total_lines"] == 3


# --- file_accessed() reporting (see docs/specs/terminal-repl.md's "Files panel" section) ---


def test_apply_reports_file_accessed_as_a_write(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    file_path = tmp_path / "new.txt"
    session = _session(tmp_path, make_session_config)

    with patch.object(session, "file_accessed") as mock_file_accessed:
        CreateFileTool(_context(tmp_path, session=session)).apply(
            {"filename": str(file_path), "content": "a\n"})

    mock_file_accessed.assert_called_once_with(str(file_path.resolve()), "write")


def test_update_args_truncates_content_on_success(tmp_path: Path) -> None:
    filename = str(tmp_path / "new.txt")
    args = {"filename": filename, "content": "a\nb\nc\n"}

    updated = CreateFileTool(_context(tmp_path)).update_args(
        args, None, ToolCallErrorInfo(is_error=False, is_retryable=False))

    assert updated == {
        "filename": filename,
        "content": "(Applied correctly; arguments truncated. See response)",
    }
