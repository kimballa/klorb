# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.replace_all."""

from pathlib import Path

import pytest

from klorb.permissions.directory_access import DirRules
from klorb.permissions.table import PermissionAskRequired
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.replace_all import ReplaceAllTool
from klorb.tools.setup_context import ToolSetupContext


def _context(
    workspace_root: Path, *, read_dirs: DirRules | None = None, write_dirs: DirRules | None = None,
) -> ToolSetupContext:
    """Defaults both `readDirs`/`writeDirs` to allowing all of `workspace_root`, since
    `evaluate_write()` now requires an explicit allow in *both* tables (see
    docs/adrs/write-verdict-is-stricter-of-read-and-write-tables.md) and most tests here are
    about ReplaceAll's own logic, not the permission system -- only the "Permission-table
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


def test_literal_replace_all_occurrences(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "foo bar foo baz foo\n")

    result = ReplaceAllTool(_context(tmp_path)).apply(
        {"filename": str(file_path), "search": "foo", "new_text": "qux"})

    assert file_path.read_text() == "qux bar qux baz qux\n"
    assert result["replacements_made"] == 3
    assert result["is_regex"] is False


def test_literal_search_treats_regex_metacharacters_literally(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a.b a.b axb\n")

    result = ReplaceAllTool(_context(tmp_path)).apply(
        {"filename": str(file_path), "search": "a.b", "new_text": "X"})

    assert file_path.read_text() == "X X axb\n"
    assert result["replacements_made"] == 2


def test_regex_replace_with_backreference(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "foo(1) foo(2)\n")

    result = ReplaceAllTool(_context(tmp_path)).apply({
        "filename": str(file_path), "search": r"foo\((\d)\)", "new_text": r"bar(\1)", "is_regex": True,
    })

    assert file_path.read_text() == "bar(1) bar(2)\n"
    assert result["replacements_made"] == 2
    assert result["is_regex"] is True


def test_replacement_scanning_resumes_after_inserted_text_not_from_within_it(tmp_path: Path) -> None:
    # If matching resumed from the start of the file, or rescanned the freshly-inserted
    # replacement text, this would loop forever: "an" is a substring of the replacement
    # "banana" itself. re.subn instead resumes scanning the *original* string right after
    # each match, so it terminates with exactly the two non-overlapping "an" occurrences in
    # "banana" replaced.
    file_path = _write(tmp_path, "sample.txt", "banana\n")

    result = ReplaceAllTool(_context(tmp_path)).apply(
        {"filename": str(file_path), "search": "an", "new_text": "banana"})

    assert file_path.read_text() == "bbananabananaa\n"
    assert result["replacements_made"] == 2


def test_case_insensitive_literal_replace(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "Foo foo FOO\n")

    result = ReplaceAllTool(_context(tmp_path)).apply({
        "filename": str(file_path), "search": "foo", "new_text": "bar", "case_insensitive": True,
    })

    assert file_path.read_text() == "bar bar bar\n"
    assert result["replacements_made"] == 3


def test_multiline_regex_anchors_match_each_line(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "one\ntwo\nthree\n")

    result = ReplaceAllTool(_context(tmp_path)).apply({
        "filename": str(file_path), "search": r"^t", "new_text": "T", "is_regex": True, "multiline": True,
    })

    assert file_path.read_text() == "one\nTwo\nThree\n"
    assert result["replacements_made"] == 2


def test_zero_matches_leaves_file_untouched(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "hello world\n")

    result = ReplaceAllTool(_context(tmp_path)).apply(
        {"filename": str(file_path), "search": "nowhere", "new_text": "x"})

    assert file_path.read_text() == "hello world\n"
    assert result["replacements_made"] == 0


def test_path_outside_workspace_root_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = _write(tmp_path, "outside.txt", "foo\n")

    with pytest.raises(PermissionError):
        ReplaceAllTool(_context(workspace)).apply(
            {"filename": str(outside), "search": "foo", "new_text": "bar"})


def test_name_and_parameters(tmp_path: Path) -> None:
    tool = ReplaceAllTool(_context(tmp_path))

    assert tool.name() == "ReplaceAll"
    assert set(tool.parameters()["required"]) == {"filename", "search", "new_text"}


# --- Permission-table integration (see docs/specs/permissions.md) ---


def test_writedirs_deny_rejects_an_otherwise_in_workspace_replace(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "foo\n")

    with pytest.raises(PermissionError):
        ReplaceAllTool(_context(tmp_path, write_dirs=DirRules(deny=[tmp_path]))).apply(
            {"filename": str(file_path), "search": "foo", "new_text": "bar"})

    assert file_path.read_text() == "foo\n"


def test_writedirs_ask_raises_permission_ask_required(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "foo\n")

    with pytest.raises(PermissionAskRequired):
        ReplaceAllTool(_context(tmp_path, write_dirs=DirRules(ask=[tmp_path]))).apply(
            {"filename": str(file_path), "search": "foo", "new_text": "bar"})

    assert file_path.read_text() == "foo\n"


def test_hard_workspace_boundary_wins_even_if_writedirs_allow_covers_outside(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = _write(tmp_path, "outside.txt", "foo\n")

    with pytest.raises(PermissionError):
        ReplaceAllTool(_context(workspace, write_dirs=DirRules(allow=[tmp_path]))).apply(
            {"filename": str(outside), "search": "foo", "new_text": "bar"})

    assert outside.read_text() == "foo\n"


def test_writedirs_allow_alone_does_not_grant_write_without_readdirs_allow(tmp_path: Path) -> None:
    """The write-only-directory bug this write/read merge exists to fix: writeDirs.allow must
    not grant write access to a path readDirs is silent on -- see
    docs/adrs/write-verdict-is-stricter-of-read-and-write-tables.md. An integration-level guard
    on top of test_permissions.py's unit-level
    test_evaluate_write_asks_when_writedirs_allows_but_readdirs_is_silent, since this file's
    other permission tests all use the (allow, allow) default context."""
    file_path = _write(tmp_path, "sample.txt", "foo\n")

    with pytest.raises(PermissionAskRequired):
        ReplaceAllTool(_context(
            tmp_path, read_dirs=DirRules(), write_dirs=DirRules(allow=[tmp_path]),
        )).apply({"filename": str(file_path), "search": "foo", "new_text": "bar"})

    assert file_path.read_text() == "foo\n"


def test_klorb_dir_write_implicitly_denied_even_with_no_config(tmp_path: Path) -> None:
    klorb_dir = tmp_path / ".klorb"
    klorb_dir.mkdir()
    file_path = _write(klorb_dir, "klorb-config.json", "foo\n")

    with pytest.raises(PermissionError):
        ReplaceAllTool(_context(tmp_path)).apply(
            {"filename": str(file_path), "search": "foo", "new_text": "bar"})

    assert file_path.read_text() == "foo\n"
