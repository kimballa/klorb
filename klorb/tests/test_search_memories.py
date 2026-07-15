# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.memory.search_memories."""

from pathlib import Path

import pytest

from klorb.permissions.table import PermissionAskRequired, Verdict
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.memory import common as memory_common_module
from klorb.tools.memory.common import Namespace, memory_namespace_dir
from klorb.tools.memory.search_memories import SearchMemoriesTool
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


def _write(context: ToolSetupContext, namespace: Namespace, filename: str, content: str) -> Path:
    namespace_dir = memory_namespace_dir(context, namespace)
    namespace_dir.mkdir(parents=True, exist_ok=True)
    path = namespace_dir / filename
    path.write_text(content)
    return path


def test_finds_case_insensitive_content_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "notes.md", "Topic\nONE\ntwo\nthree\n")

    result = SearchMemoriesTool(context).apply({"queries": ["one"]})

    assert result["match_count"] == 1
    assert result["results"][0] == {
        "namespace": "global", "filename": "notes.md", "lines": ["*2|ONE"]}


def test_multiple_queries_combine_via_alternation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "notes.md", "Topic\nalpha\nbeta\ndelta\n")

    result = SearchMemoriesTool(context).apply({"queries": ["alpha", "delta"]})

    # Both matches live in one file, so they group into a single result entry whose two
    # dense-format lines each carry a `*`; match_count still counts the individual matches.
    assert result["match_count"] == 2
    assert len(result["results"]) == 1
    assert result["results"][0] == {
        "namespace": "global", "filename": "notes.md", "lines": ["*2|alpha", "*4|delta"]}


def test_searches_both_namespaces_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "a.md", "Topic\nMATCH\n")
    _write(context, "workspace", "b.md", "Topic\nMATCH\n")

    result = SearchMemoriesTool(context).apply({"queries": ["MATCH"]})

    namespaces = {hit["namespace"] for hit in result["results"]}
    assert namespaces == {"global", "workspace"}


def test_filename_only_match_reports_first_non_blank_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "you-like-birds.md", "\n   \nActual topic\nno matching word here\n")

    result = SearchMemoriesTool(context).apply({"queries": ["bird"]})

    assert result["match_count"] == 1
    assert result["results"][0] == {
        "namespace": "global", "filename": "you-like-birds.md", "lines": [" 3|Actual topic"],
    }


def test_filename_and_content_match_reports_once_with_real_content_hits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "you-like-birds.md", "Topic\nbird watching notes\n")

    result = SearchMemoriesTool(context).apply({"queries": ["bird"]})

    assert result["match_count"] == 1
    assert result["results"][0] == {
        "namespace": "global", "filename": "you-like-birds.md",
        "lines": ["*2|bird watching notes"],
    }


def test_untrusted_workspace_yields_no_workspace_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, trusted=False)
    _write(context, "workspace", "secret.md", "Topic\nMATCH\n")

    result = SearchMemoriesTool(context).apply({"queries": ["MATCH"]})

    assert result["results"] == []


def test_empty_namespace_returns_no_hits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)

    result = SearchMemoriesTool(context).apply({"queries": ["anything"]})

    assert result["match_count"] == 0
    assert result["results"] == []


def test_empty_queries_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="non-empty array"):
        SearchMemoriesTool(context).apply({"queries": []})


def test_non_string_query_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="must be a string"):
        SearchMemoriesTool(context).apply({"queries": [1]})


def test_read_permission_deny_raises_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, read_permission="deny")

    with pytest.raises(PermissionError):
        SearchMemoriesTool(context).apply({"queries": ["x"]})


def test_read_permission_ask_raises_permission_ask_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch, read_permission="ask")

    with pytest.raises(PermissionAskRequired):
        SearchMemoriesTool(context).apply({"queries": ["x"]})


def test_name_and_parameters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = SearchMemoriesTool(_context(tmp_path, monkeypatch))

    assert tool.name() == "SearchMemories"
    assert tool.parameters()["required"] == ["queries"]
    assert "namespace" not in tool.parameters()["properties"]


def test_summary_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "a.md", "Topic\nhello\n")
    tool = SearchMemoriesTool(context)

    result = tool.apply({"queries": ["hello"]})

    assert "1 match" in tool.summary({"queries": ["hello"]}, result)


def test_summary_on_failure_includes_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = SearchMemoriesTool(_context(tmp_path, monkeypatch))

    assert tool.summary({"queries": ["x"]}, error="boom") == "Search memories: ['x'] failed: boom"


# --- outputStyle tests ---


def test_default_output_style_is_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When outputStyle is omitted, the default is 'Matches' (only hit lines, no context)."""
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "notes.md", "Topic\nONE\ntwo\nthree\n")
    result = SearchMemoriesTool(context).apply({"queries": ["one"]})
    assert result["match_count"] == 1
    assert result["results"][0] == {
        "namespace": "global", "filename": "notes.md", "lines": ["*2|ONE"]}


def test_output_style_matches_returns_only_hit_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "notes.md", "Topic\nalpha\nbeta\ndelta\n")
    result = SearchMemoriesTool(context).apply(
        {"queries": ["alpha", "delta"], "outputStyle": "Matches"})
    assert result["match_count"] == 2
    assert result["results"][0] == {
        "namespace": "global", "filename": "notes.md",
        "lines": ["*2|alpha", "*4|delta"]}


def test_output_style_full_context_returns_surrounding_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "notes.md", "line1\nline2\nMATCH\nline4\nline5\n")
    result = SearchMemoriesTool(context).apply(
        {"queries": ["MATCH"], "outputStyle": "FullContext"})
    # FullContext uses grep_context_lines (default 2) so should include lines 1-5
    assert result["match_count"] == 1
    lines = result["results"][0]["lines"]
    # Should have context lines (space-prefixed) around the match
    assert len(lines) > 1
    # The matched line should be present
    assert any(line.startswith("*") for line in lines)


def test_output_style_list_files_returns_deduplicated_filenames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "a.md", "Topic\nhello\n")
    _write(context, "global", "b.md", "Topic\nhello\n")
    _write(context, "global", "c.md", "Topic\nnothing here\n")
    result = SearchMemoriesTool(context).apply(
        {"queries": ["hello"], "outputStyle": "ListFiles"})
    # files should be a plain list of strings
    assert isinstance(result["files"], list)
    assert all(isinstance(f, str) for f in result["files"])
    # Should have namespace/filename format
    assert result["files"] == ["global/a.md", "global/b.md"]
    # No results key, just files
    assert "results" not in result
    assert result["match_count"] == 2


def test_list_files_includes_filename_only_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "you-like-birds.md", "Topic\nno matching word here\n")
    result = SearchMemoriesTool(context).apply(
        {"queries": ["bird"], "outputStyle": "ListFiles"})
    assert result["files"] == ["global/you-like-birds.md"]
    assert result["match_count"] == 1


def test_list_files_filename_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ListFiles format is <namespace>/<filename>, not a full path."""
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "notes.md", "Topic\nhello\n")
    result = SearchMemoriesTool(context).apply(
        {"queries": ["hello"], "outputStyle": "ListFiles"})
    assert result["files"] == ["global/notes.md"]


def test_invalid_output_style_raises_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "a.md", "Topic\nhello\n")
    with pytest.raises(ValueError, match="outputStyle"):
        SearchMemoriesTool(context).apply(
            {"queries": ["hello"], "outputStyle": "bogus"})


def test_invalid_output_style_error_explains_enum_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    _write(context, "global", "a.md", "Topic\nhello\n")
    with pytest.raises(ValueError, match="outputStyle") as exc_info:
        SearchMemoriesTool(context).apply(
            {"queries": ["hello"], "outputStyle": "bad"})
    msg = str(exc_info.value)
    assert "ListFiles" in msg
    assert "Matches" in msg
    assert "FullContext" in msg
