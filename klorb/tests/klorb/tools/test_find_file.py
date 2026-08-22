# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.find_file."""
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from klorb.permissions.directory_access import DirRules
from klorb.permissions.table import PermissionAskRequired
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig, WorkspaceAccess
from klorb.tools.find_file import FindFileTool
from klorb.tools.setup_context import ToolSetupContext
from klorb.workspace import Workspace


def _context(
    workspace_root: Path,
    *,
    max_results: int = 500,
    read_dirs: DirRules | None = None,
) -> ToolSetupContext:
    return ToolSetupContext(
        process_config=ProcessConfig(find_file_max_results=max_results),
        session_config=SessionConfig(workspace_access=WorkspaceAccess(
            workspace=Workspace(path=workspace_root),
            read_dirs=read_dirs or DirRules(),
            write_dirs=DirRules(),
        )),
    )


def _make_tree(root: Path) -> None:
    (root / "top.py").write_text("top\n")
    (root / "readme.md").write_text("readme\n")
    (root / "sub").mkdir()
    (root / "sub" / "nested.py").write_text("nested\n")
    (root / "sub" / "deep").mkdir()
    (root / "sub" / "deep" / "leaf_context.py").write_text("leaf\n")


def _file_names(matches: list[dict[str, str]]) -> set[str]:
    return {Path(m["file"]).name for m in matches if "file" in m}


def _dir_names(matches: list[dict[str, str]]) -> set[str]:
    return {Path(m["dir"]).name for m in matches if "dir" in m}


def test_finds_files_matching_glob_across_the_tree(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = FindFileTool(_context(tmp_path)).apply({"dirname": "", "pattern": "*.py"})

    assert _file_names(result["matches"]) == {"top.py", "nested.py", "leaf_context.py"}
    assert result["truncated"] is False


def test_substring_glob_pattern(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = FindFileTool(_context(tmp_path)).apply({"dirname": "", "pattern": "*_context*"})

    assert _file_names(result["matches"]) == {"leaf_context.py"}


def test_exact_name_pattern_matches_only_that_name(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = FindFileTool(_context(tmp_path)).apply({"dirname": "", "pattern": "readme.md"})

    assert _file_names(result["matches"]) == {"readme.md"}


def test_case_insensitive_flag(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = FindFileTool(_context(tmp_path)).apply(
        {"dirname": "", "pattern": "*.PY", "case_insensitive": True})

    assert _file_names(result["matches"]) == {"top.py", "nested.py", "leaf_context.py"}


def test_max_results_truncates(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = FindFileTool(_context(tmp_path, max_results=1)).apply(
        {"dirname": "", "pattern": "*.py"})

    assert len(result["matches"]) == 1
    assert result["truncated"] is True


def test_matching_directory_is_returned_as_its_own_dir_entry(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    (tmp_path / "sub" / "system_prompts.d").mkdir()
    (tmp_path / "sub" / "system_prompts.d" / "one.txt").write_text("one\n")

    result = FindFileTool(_context(tmp_path)).apply(
        {"dirname": "", "pattern": "*system_prompt*"})

    assert _dir_names(result["matches"]) == {"system_prompts.d"}


def test_directory_match_does_not_stop_descent_into_it(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    matching_dir = tmp_path / "sub" / "match.d"
    matching_dir.mkdir()
    (matching_dir / "match.d.txt").write_text("inner\n")

    result = FindFileTool(_context(tmp_path)).apply({"dirname": "", "pattern": "match.d*"})

    assert _dir_names(result["matches"]) == {"match.d"}
    assert _file_names(result["matches"]) == {"match.d.txt"}


def test_denied_root_raises_permission_error(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(PermissionError):
        FindFileTool(_context(tmp_path, read_dirs=DirRules(deny=[tmp_path]))).apply(
            {"dirname": "", "pattern": "*.py"})


def test_ask_root_raises_permission_ask_required(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(PermissionAskRequired):
        FindFileTool(_context(tmp_path, read_dirs=DirRules(ask=[tmp_path]))).apply(
            {"dirname": "", "pattern": "*.py"})


def test_denied_subdir_is_skipped_not_raised(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = FindFileTool(_context(tmp_path, read_dirs=DirRules(deny=[tmp_path / "sub"]))).apply(
        {"dirname": "", "pattern": "*.py"})

    names = _file_names(result["matches"])
    assert names == {"top.py"}


def test_gitignored_match_is_hidden_and_flagged(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    (tmp_path / ".gitignore").write_text("sub/\n")

    result = FindFileTool(_context(tmp_path)).apply({"dirname": "", "pattern": "*.py"})

    names = _file_names(result["matches"])
    assert names == {"top.py"}
    assert result["gitignored_hidden"] is True
    assert result["use_gitignore"] is True
    assert "use_gitignore=false" in result["note"]


def test_gitignored_directory_name_match_is_hidden_and_flagged(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    (tmp_path / "system_prompts.d").mkdir()
    (tmp_path / ".gitignore").write_text("system_prompts.d/\n")

    result = FindFileTool(_context(tmp_path)).apply(
        {"dirname": "", "pattern": "*system_prompt*"})

    assert result["matches"] == []
    assert result["gitignored_hidden"] is True
    assert "use_gitignore=false" in result["note"]


def test_use_gitignore_false_reveals_gitignored_directory_match(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    (tmp_path / "system_prompts.d").mkdir()
    (tmp_path / ".gitignore").write_text("system_prompts.d/\n")

    result = FindFileTool(_context(tmp_path)).apply(
        {"dirname": "", "pattern": "*system_prompt*", "use_gitignore": False})

    assert _dir_names(result["matches"]) == {"system_prompts.d"}
    assert result["gitignored_hidden"] is False


def test_use_gitignore_false_includes_ignored_matches(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    (tmp_path / ".gitignore").write_text("sub/\n")

    result = FindFileTool(_context(tmp_path)).apply(
        {"dirname": "", "pattern": "*.py", "use_gitignore": False})

    names = _file_names(result["matches"])
    assert names == {"top.py", "nested.py", "leaf_context.py"}
    assert result["gitignored_hidden"] is False
    assert "note" not in result


def test_find_file_is_interrupted_by_the_turn_cancel_event(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """An already-set `Session.active_cancel_event` (a Ctrl+C/Escape interrupt) stops the search
    at the first directory and reports `cancelled: True` rather than walking the whole tree —
    see `InterruptibleTool`."""
    _make_tree(tmp_path)
    session_config = make_session_config(
        workspace=Workspace(path=tmp_path), read_dirs=DirRules(), write_dirs=DirRules())
    session = Session(config=session_config)
    try:
        session.active_cancel_event = threading.Event()
        session.active_cancel_event.set()
        context = ToolSetupContext(
            process_config=ProcessConfig(find_file_max_results=500),
            session_config=session_config, session=session)

        result = FindFileTool(context).apply({"dirname": "", "pattern": "*.py"})

        assert result["cancelled"] is True
        assert result["matches"] == []
    finally:
        session.close()


def test_find_file_cancelled_is_false_on_a_normal_run(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    result = FindFileTool(_context(tmp_path)).apply({"dirname": "", "pattern": "*.py"})
    assert result["cancelled"] is False


def test_no_gitignore_hidden_flag_when_nothing_ignored(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = FindFileTool(_context(tmp_path)).apply({"dirname": "", "pattern": "*.py"})

    assert result["gitignored_hidden"] is False
    assert "note" not in result


def test_no_gitignore_hidden_flag_when_ignored_files_do_not_match_pattern(tmp_path: Path) -> None:
    """A gitignored directory that holds no file matching the glob must NOT set
    gitignored_hidden — the flag means "a match was hidden," not "something was ignored."
    Here `sub/` (with only `*.py` files) is gitignored while we search for `*.md`."""
    _make_tree(tmp_path)
    (tmp_path / ".gitignore").write_text("sub/\n")

    result = FindFileTool(_context(tmp_path)).apply({"dirname": "", "pattern": "*.md"})

    names = _file_names(result["matches"])
    assert names == {"readme.md"}
    assert result["gitignored_hidden"] is False
    assert "note" not in result


def test_summary_on_success(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    tool = FindFileTool(_context(tmp_path))
    result = tool.apply({"dirname": "", "pattern": "*.py"})

    summary = tool.summary({"dirname": "", "pattern": "*.py"}, result)
    assert "*.py" in summary
    assert "3 matches" in summary


def test_summary_on_failure_includes_the_error() -> None:
    tool = FindFileTool(_context(Path("/tmp")))

    assert tool.summary({"dirname": "", "pattern": "*.py"}, error="not found") == (
        "Find file: '*.py' failed: not found")


def test_omitted_dirname_defaults_to_workspace_root(tmp_path: Path) -> None:
    """Calling without dirname should behave identically to dirname=""."""
    _make_tree(tmp_path)

    result_with_empty = FindFileTool(_context(tmp_path)).apply(
        {"dirname": "", "pattern": "*.py"})
    result_omitted = FindFileTool(_context(tmp_path)).apply(
        {"pattern": "*.py"})

    assert result_omitted["matches"] == result_with_empty["matches"]
    assert result_omitted["truncated"] is False
