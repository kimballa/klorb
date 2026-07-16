# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.grep."""

from pathlib import Path

import pytest

from klorb.permissions.directory_access import DirRules
from klorb.permissions.table import PermissionAskRequired
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.grep import GrepTool
from klorb.tools.setup_context import ToolSetupContext
from klorb.workspace import Workspace


def _context(
    workspace_root: Path,
    *,
    max_results: int = 500,
    context_lines: int = 2,
    read_dirs: DirRules | None = None,
) -> ToolSetupContext:
    return ToolSetupContext(
        process_config=ProcessConfig(grep_max_results=max_results, grep_context_lines=context_lines),
        session_config=SessionConfig(
            workspace=Workspace(path=workspace_root),
            read_dirs=read_dirs or DirRules(),
            write_dirs=DirRules(),
        ),
    )


def _make_tree(root: Path) -> None:
    (root / "top.py").write_text("import os\nprint('hello world')\n")
    (root / "sub").mkdir()
    (root / "sub" / "nested.py").write_text("def hello():\n    return 'hello again'\n")
    (root / "sub" / "notes.txt").write_text("hello from notes\n")
    (root / "sub" / "binary.dat").write_bytes(b"\xff\xfe\x00binary hello\x00")


def _matched_filenames(result: dict) -> set:
    return {Path(file["filename"]).name for file in result["files"]}


def _parse_lines(file: dict) -> list[tuple[int, str, bool]]:
    """Parse a file's dense-format lines.

    Returns list of (line_number, line_content, is_matched) tuples.
    """
    result = []
    for line_str in file["lines"]:
        # Format is " 123|content" or "*123|content"
        marker = line_str[0]
        rest = line_str[1:]
        pipe_idx = rest.index("|")
        line_number = int(rest[:pipe_idx])
        content = rest[pipe_idx + 1:]
        is_matched = marker == "*"
        result.append((line_number, content, is_matched))
    return result


def test_finds_literal_matches_across_the_tree(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path)).apply({"path": "", "queries": ["hello"]})

    assert _matched_filenames(result) == {"top.py", "nested.py", "notes.txt"}
    assert result["match_count"] == 4
    assert result["truncated"] is False


def test_binary_files_are_skipped(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path)).apply({"path": "", "queries": ["hello"]})

    assert "binary.dat" not in _matched_filenames(result)


def test_case_insensitive_flag(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path)).apply(
        {"path": "", "queries": ["HELLO WORLD"], "case_insensitive": True})

    assert "top.py" in _matched_filenames(result)


def test_multiple_queries_match_on_any_hit(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path)).apply(
        {"path": "", "queries": ["import os", "from notes"]})

    assert _matched_filenames(result) == {"top.py", "notes.txt"}
    assert result["match_count"] == 2


def test_regex_pattern(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path)).apply(
        {"path": "", "queries": [r"^def \w+\(\):"], "is_regex": True})

    assert result["match_count"] == 1
    file = result["files"][0]
    assert file["filename"].endswith("nested.py")
    parsed = _parse_lines(file)
    matched_lines = [line for line in parsed if line[2]]
    assert len(matched_lines) == 1
    assert matched_lines[0][0] == 1


def test_multiple_regex_queries_are_distinct_alternatives(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path)).apply(
        {"path": "", "queries": [r"^def \w+\(\):", r"^import \w+"], "is_regex": True})

    assert result["match_count"] == 2
    assert _matched_filenames(result) == {"top.py", "nested.py"}


def test_invalid_regex_raises_value_error(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(ValueError, match="Invalid regex"):
        GrepTool(_context(tmp_path)).apply({"path": "", "queries": ["("], "is_regex": True})


def test_empty_queries_raises_value_error(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(ValueError, match="queries"):
        GrepTool(_context(tmp_path)).apply({"path": "", "queries": []})


def test_file_glob_filters_searched_files(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path)).apply(
        {"path": "", "queries": ["hello"], "file_glob": "*.py"})

    assert _matched_filenames(result) == {"top.py", "nested.py"}


def test_context_lines_surround_each_match(tmp_path: Path) -> None:
    (tmp_path / "lines.txt").write_text("a\nb\nMATCH\nc\nd\n")

    result = GrepTool(_context(tmp_path, context_lines=1)).apply(
        {"path": "", "queries": ["MATCH"], "outputStyle": "FullContext"})

    assert len(result["files"]) == 1
    parsed = _parse_lines(result["files"][0])
    assert [line[0] for line in parsed] == [2, 3, 4]
    assert [line[1] for line in parsed] == ["b", "MATCH", "c"]
    assert [line[2] for line in parsed] == [False, True, False]


def test_adjacent_matches_merge_into_one_block(tmp_path: Path) -> None:
    (tmp_path / "lines.txt").write_text("MATCH\nx\nMATCH\n")

    result = GrepTool(_context(tmp_path, context_lines=1)).apply(
        {"path": "", "queries": ["MATCH"], "outputStyle": "FullContext"})

    assert len(result["files"]) == 1
    parsed = _parse_lines(result["files"][0])
    assert [line[0] for line in parsed] == [1, 2, 3]
    assert result["match_count"] == 2


def test_distant_matches_in_one_file_flatten_with_a_line_number_gap(tmp_path: Path) -> None:
    lines = ["filler"] * 20
    lines[0] = "MATCH"
    lines[19] = "MATCH"
    (tmp_path / "lines.txt").write_text("\n".join(lines) + "\n")

    result = GrepTool(_context(tmp_path, context_lines=1)).apply(
        {"path": "", "queries": ["MATCH"], "outputStyle": "FullContext"})

    # One file entry, its two non-contiguous windows concatenated into a single lines array;
    # the gap is visible only as a jump in the embedded line numbers, with no separator.
    assert len(result["files"]) == 1
    parsed = _parse_lines(result["files"][0])
    assert [line[0] for line in parsed] == [1, 2, 19, 20]
    assert [line[2] for line in parsed] == [True, False, False, True]


def test_max_results_truncates(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path, max_results=1)).apply(
        {"path": "", "queries": ["hello"]})

    assert result["match_count"] == 1
    assert result["truncated"] is True


def test_max_results_exact_count_is_not_truncated(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path, max_results=4)).apply(
        {"path": "", "queries": ["hello"]})

    assert result["match_count"] == 4
    assert result["truncated"] is False


def test_denied_root_raises_permission_error(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(PermissionError):
        GrepTool(_context(tmp_path, read_dirs=DirRules(deny=[tmp_path]))).apply(
            {"path": "", "queries": ["hello"]})


def test_ask_root_raises_permission_ask_required(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    with pytest.raises(PermissionAskRequired):
        GrepTool(_context(tmp_path, read_dirs=DirRules(ask=[tmp_path]))).apply(
            {"path": "", "queries": ["hello"]})


def test_denied_subdir_is_skipped_not_raised(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = GrepTool(_context(tmp_path, read_dirs=DirRules(deny=[tmp_path / "sub"]))).apply(
        {"path": "", "queries": ["hello"]})

    assert _matched_filenames(result) == {"top.py"}


def test_summary_on_success(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    tool = GrepTool(_context(tmp_path))
    result = tool.apply({"path": "", "queries": ["hello"]})

    summary = tool.summary({"path": "", "queries": ["hello"]}, result)
    assert "hello" in summary
    assert "4 matches" in summary


def test_summary_on_failure_includes_the_error() -> None:
    tool = GrepTool(_context(Path("/tmp")))

    assert tool.summary({"path": "", "queries": ["hello"]}, error="not found") == (
        "Grep: ['hello'] failed: not found")


# --- outputStyle tests ---


def test_default_output_style_is_matches(tmp_path: Path) -> None:
    """When outputStyle is omitted, the default is 'Matches' (only hit lines, no context)."""
    (tmp_path / "lines.txt").write_text("a\nb\nMATCH\nc\nd\n")
    result = GrepTool(_context(tmp_path, context_lines=1)).apply(
        {"path": "", "queries": ["MATCH"]})
    parsed = _parse_lines(result["files"][0])
    # Only the hit line, no surrounding context.
    assert [line[0] for line in parsed] == [3]
    assert [line[2] for line in parsed] == [True]


def test_output_style_matches_returns_only_hit_lines(tmp_path: Path) -> None:
    (tmp_path / "lines.txt").write_text("a\nb\nMATCH\nc\nd\n")
    result = GrepTool(_context(tmp_path, context_lines=1)).apply(
        {"path": "", "queries": ["MATCH"], "outputStyle": "Matches"})
    parsed = _parse_lines(result["files"][0])
    assert [line[0] for line in parsed] == [3]
    assert [line[2] for line in parsed] == [True]


def test_output_style_full_context_returns_surrounding_lines(tmp_path: Path) -> None:
    (tmp_path / "lines.txt").write_text("a\nb\nMATCH\nc\nd\n")
    result = GrepTool(_context(tmp_path, context_lines=1)).apply(
        {"path": "", "queries": ["MATCH"], "outputStyle": "FullContext"})
    parsed = _parse_lines(result["files"][0])
    assert [line[0] for line in parsed] == [2, 3, 4]
    assert [line[2] for line in parsed] == [False, True, False]


def test_output_style_list_files_returns_deduplicated_filenames(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    result = GrepTool(_context(tmp_path)).apply(
        {"path": "", "queries": ["hello"], "outputStyle": "ListFiles"})
    # files should be a plain list of strings, not dicts
    assert isinstance(result["files"], list)
    assert all(isinstance(f, str) for f in result["files"])
    names = {Path(f).name for f in result["files"]}
    assert names == {"top.py", "nested.py", "notes.txt"}
    # lines should NOT be in the result
    assert "context_lines" not in result
    assert result["match_count"] == 4


def test_output_style_empty_string_defaults_to_matches(tmp_path: Path) -> None:
    (tmp_path / "lines.txt").write_text("a\nb\nMATCH\nc\nd\n")
    result = GrepTool(_context(tmp_path, context_lines=1)).apply(
        {"path": "", "queries": ["MATCH"], "outputStyle": ""})
    parsed = _parse_lines(result["files"][0])
    assert [line[0] for line in parsed] == [3]


def test_output_style_none_defaults_to_matches(tmp_path: Path) -> None:
    (tmp_path / "lines.txt").write_text("a\nb\nMATCH\nc\nd\n")
    result = GrepTool(_context(tmp_path, context_lines=1)).apply(
        {"path": "", "queries": ["MATCH"], "outputStyle": None})
    parsed = _parse_lines(result["files"][0])
    assert [line[0] for line in parsed] == [3]


def test_invalid_output_style_raises_value_error(tmp_path: Path) -> None:
    (tmp_path / "lines.txt").write_text("a\nb\nMATCH\nc\nd\n")
    with pytest.raises(ValueError, match="outputStyle"):
        GrepTool(_context(tmp_path)).apply(
            {"path": "", "queries": ["MATCH"], "outputStyle": "invalid"})


def test_invalid_output_style_error_explains_enum_values(tmp_path: Path) -> None:
    (tmp_path / "lines.txt").write_text("a\nb\nMATCH\nc\nd\n")
    with pytest.raises(ValueError, match="outputStyle") as exc_info:
        GrepTool(_context(tmp_path)).apply(
            {"path": "", "queries": ["MATCH"], "outputStyle": "bad"})
    msg = str(exc_info.value)
    assert "ListFiles" in msg
    assert "Matches" in msg
    assert "FullContext" in msg


# --- single-file search tests ---


def test_search_single_file_finds_match(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hello world\nnothing here\n")
    result = GrepTool(_context(tmp_path)).apply(
        {"path": str(tmp_path / "hello.txt"), "queries": ["hello"]})
    assert result["match_count"] == 1
    assert len(result["files"]) == 1
    assert result["files"][0]["filename"] == str(tmp_path / "hello.txt")
    parsed = _parse_lines(result["files"][0])
    assert parsed[0][1] == "hello world"


def test_search_single_file_no_match(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hello world\n")
    result = GrepTool(_context(tmp_path)).apply(
        {"path": str(tmp_path / "hello.txt"), "queries": ["nope"]})
    assert result["match_count"] == 0
    assert result["files"] == []


def test_search_single_file_list_files_mode(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hello world\n")
    result = GrepTool(_context(tmp_path)).apply(
        {"path": str(tmp_path / "hello.txt"), "queries": ["hello"],
         "outputStyle": "ListFiles"})
    assert result["files"] == [str(tmp_path / "hello.txt")]
    assert result["match_count"] == 1


def test_search_single_file_full_context_mode(tmp_path: Path) -> None:
    (tmp_path / "lines.txt").write_text("a\nb\nMATCH\nc\nd\n")
    result = GrepTool(_context(tmp_path, context_lines=1)).apply(
        {"path": str(tmp_path / "lines.txt"), "queries": ["MATCH"],
         "outputStyle": "FullContext"})
    parsed = _parse_lines(result["files"][0])
    assert [line[0] for line in parsed] == [2, 3, 4]


def test_search_single_file_ignores_file_glob(tmp_path: Path) -> None:
    """file_glob is irrelevant when searching a specific file."""
    (tmp_path / "hello.txt").write_text("hello world\n")
    result = GrepTool(_context(tmp_path)).apply(
        {"path": str(tmp_path / "hello.txt"), "queries": ["hello"],
         "file_glob": "*.py"})  # Would exclude .txt in dir mode
    assert result["match_count"] == 1


def test_search_single_file_nonexistent_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        GrepTool(_context(tmp_path)).apply(
            {"path": str(tmp_path / "nope.txt"), "queries": ["hello"]})


def test_search_single_file_permission_denied(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hello world\n")
    with pytest.raises(PermissionError):
        GrepTool(_context(tmp_path, read_dirs=DirRules(deny=[tmp_path]))).apply(
            {"path": str(tmp_path / "hello.txt"), "queries": ["hello"]})


def test_empty_path_still_searches_whole_tree(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    result = GrepTool(_context(tmp_path)).apply(
        {"path": "", "queries": ["hello"]})
    assert _matched_filenames(result) == {"top.py", "nested.py", "notes.txt"}
    assert result["match_count"] == 4


def test_none_path_still_searches_whole_tree(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    result = GrepTool(_context(tmp_path)).apply(
        {"path": None, "queries": ["hello"]})
    assert _matched_filenames(result) == {"top.py", "nested.py", "notes.txt"}
    assert result["match_count"] == 4


def test_directory_path_still_recurses(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    result = GrepTool(_context(tmp_path)).apply(
        {"path": str(tmp_path), "queries": ["hello"]})
    assert _matched_filenames(result) == {"top.py", "nested.py", "notes.txt"}
    assert result["match_count"] == 4


def test_gitignored_file_is_skipped_and_flagged(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    (tmp_path / ".gitignore").write_text("sub/\n")

    result = GrepTool(_context(tmp_path)).apply({"path": "", "queries": ["hello"]})

    assert _matched_filenames(result) == {"top.py"}
    assert result["gitignored_hidden"] is True
    assert result["use_gitignore"] is True
    assert "use_gitignore=false" in result["note"]


def test_use_gitignore_false_searches_ignored_files(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    (tmp_path / ".gitignore").write_text("sub/\n")

    result = GrepTool(_context(tmp_path)).apply(
        {"path": "", "queries": ["hello"], "use_gitignore": False})

    assert _matched_filenames(result) == {"top.py", "nested.py", "notes.txt"}
    assert result["gitignored_hidden"] is False
    assert "note" not in result


def test_gitignore_flag_in_list_files_mode(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    (tmp_path / ".gitignore").write_text("sub/\n")

    result = GrepTool(_context(tmp_path)).apply(
        {"path": "", "queries": ["hello"], "outputStyle": "ListFiles"})

    assert {Path(f).name for f in result["files"]} == {"top.py"}
    assert result["gitignored_hidden"] is True
    assert "use_gitignore=false" in result["note"]


def test_explicit_single_file_ignores_gitignore_filtering(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    (tmp_path / ".gitignore").write_text("sub/\n")

    # A gitignored file named explicitly is still searched; the tree filter doesn't apply.
    result = GrepTool(_context(tmp_path)).apply(
        {"path": str(tmp_path / "sub" / "nested.py"), "queries": ["hello"]})

    assert _matched_filenames(result) == {"nested.py"}
    assert result["gitignored_hidden"] is False
