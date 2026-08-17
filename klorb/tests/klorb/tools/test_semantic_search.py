# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.semantic_search."""
from collections.abc import Callable
from pathlib import Path

import pytest

from klorb.permissions.directory_access import DirRules
from klorb.process_config import ProcessConfig
from klorb.search_index.chunk import Chunk, ChunkKind
from klorb.session import Session, SessionConfig
from klorb.tools.exceptions import ToolCallError
from klorb.tools.semantic_search import SemanticSearchTool
from klorb.tools.setup_context import ToolSetupContext
from klorb.workspace import Workspace


def _context_with_session(
    workspace_root: Path, make_session_config: Callable[..., SessionConfig]
) -> tuple[Session, ToolSetupContext]:
    """A `ToolSetupContext` backed by a real `Session`. Caller must `session.close()` when done."""
    session_config = make_session_config(
        workspace=Workspace(path=workspace_root), read_dirs=DirRules(), write_dirs=DirRules())
    session = Session(config=session_config)
    context = ToolSetupContext(
        process_config=ProcessConfig(), session_config=session_config, session=session)
    return session, context


def _make_tree(root: Path) -> None:
    (root / "top.py").write_text("import os\nprint('hello world')\n")
    (root / "sub").mkdir()
    (root / "sub" / "nested.py").write_text("def hello():\n    return 'hello again'\n")
    (root / "sub" / "notes.txt").write_text("hello from notes\n")


def _matched_filenames(result: dict) -> set:
    return {Path(file["filename"]).name for file in result["files"]}


def _parse_lines(file: dict) -> list[tuple[int, str, bool]]:
    result = []
    for line_str in file["lines"]:
        marker = line_str[0]
        rest = line_str[1:]
        pipe_idx = rest.index("|")
        line_number = int(rest[:pipe_idx])
        content = rest[pipe_idx + 1:]
        result.append((line_number, content, marker == "*"))
    return result


class _FakeWorkspaceIndexer:
    """A stand-in for `klorb.search_index.indexer.WorkspaceIndexer` that returns a fixed set of
    `(Chunk, score)` hits regardless of query text, so `SemanticSearchTool`'s merge logic can be
    tested without a real embedding model or SQLite index."""

    def __init__(self, hits: list[tuple[Chunk, float]]) -> None:
        self._hits = hits

    def hybrid_search(
        self, query_text: str, limit: int = 20, catalog: str = "workspace",
    ) -> list[tuple[Chunk, float]]:
        return self._hits[:limit]


def _chunk(source_path: str, kind: ChunkKind, start_line: int, end_line: int, text: str) -> Chunk:
    return Chunk.create(
        catalog="workspace", source_path=source_path, kind=kind,
        start_line=start_line, end_line=end_line, text=text)


def test_returns_a_file_entry_for_a_chunk_hit(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    _make_tree(tmp_path)
    chunk = _chunk("sub/nested.py", "function", 1, 2, "def hello():\n    return 'hello again'")
    session, context = _context_with_session(tmp_path, make_session_config)
    session._workspace_indexer = _FakeWorkspaceIndexer(  # type: ignore[assignment]
        [(chunk, 0.5)])
    try:
        result = SemanticSearchTool(context).apply(
            {"path": "", "queries": ["a function that greets"]})
    finally:
        session.close()

    matches = [f for f in result["files"] if f["filename"].endswith("nested.py")]
    assert len(matches) == 1
    entry = matches[0]
    assert entry["score"] == 0.5
    parsed = _parse_lines(entry)
    assert [line_number for line_number, _text, matched in parsed if matched] == [1, 2]


def test_merges_multiple_chunks_from_the_same_file(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    _make_tree(tmp_path)
    (tmp_path / "sub" / "many.py").write_text("def a():\n    pass\ndef b():\n    pass\n")
    chunks = [
        (_chunk("sub/many.py", "function", 1, 2, "def a():\n    pass"), 0.9),
        (_chunk("sub/many.py", "function", 3, 4, "def b():\n    pass"), 0.7),
    ]
    session, context = _context_with_session(tmp_path, make_session_config)
    session._workspace_indexer = _FakeWorkspaceIndexer(  # type: ignore[assignment]
        chunks)
    try:
        result = SemanticSearchTool(context).apply({"path": "", "queries": ["a function"]})
    finally:
        session.close()

    matches = [f for f in result["files"] if f["filename"].endswith("many.py")]
    assert len(matches) == 1
    assert matches[0]["score"] == 0.9
    parsed = _parse_lines(matches[0])
    assert [line_number for line_number, _text, matched in parsed if matched] == [1, 2, 3, 4]


def test_respects_path_scope(tmp_path: Path, make_session_config: Callable[..., SessionConfig]) -> None:
    _make_tree(tmp_path)
    out_of_scope_chunk = _chunk("top.py", "toplevel", 1, 2, "import os\nprint('hello world')")
    session, context = _context_with_session(tmp_path, make_session_config)
    session._workspace_indexer = _FakeWorkspaceIndexer(  # type: ignore[assignment]
        [(out_of_scope_chunk, 0.5)])
    try:
        result = SemanticSearchTool(context).apply(
            {"path": str(tmp_path / "sub"), "queries": ["something"]})
    finally:
        session.close()

    assert "top.py" not in _matched_filenames(result)


def test_respects_file_glob(tmp_path: Path, make_session_config: Callable[..., SessionConfig]) -> None:
    _make_tree(tmp_path)
    txt_chunk = _chunk("sub/notes.txt", "window", 1, 1, "hello from notes")
    session, context = _context_with_session(tmp_path, make_session_config)
    session._workspace_indexer = _FakeWorkspaceIndexer(  # type: ignore[assignment]
        [(txt_chunk, 0.5)])
    try:
        result = SemanticSearchTool(context).apply(
            {"path": "", "queries": ["something"], "file_glob": "*.py"})
    finally:
        session.close()

    assert "notes.txt" not in _matched_filenames(result)


def test_without_a_workspace_indexer_raises_tool_call_error(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    _make_tree(tmp_path)
    session, context = _context_with_session(tmp_path, make_session_config)
    assert session.workspace_indexer is None
    try:
        with pytest.raises(ToolCallError, match="search index"):
            SemanticSearchTool(context).apply({"path": "", "queries": ["hello"]})
    finally:
        session.close()


def test_top_k_caps_returned_chunks(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    _make_tree(tmp_path)
    (tmp_path / "sub" / "many.py").write_text(
        "\n".join(f"def fn_{i}():\n    return {i}" for i in range(20)))
    chunks = [
        (_chunk("sub/many.py", "function", i * 2 + 1, i * 2 + 2, f"def fn_{i}():\n    return {i}"),
         1.0 - i * 0.01)
        for i in range(20)
    ]
    session, context = _context_with_session(tmp_path, make_session_config)
    session._workspace_indexer = _FakeWorkspaceIndexer(  # type: ignore[assignment]
        chunks)
    try:
        result = SemanticSearchTool(context).apply(
            {"path": "", "queries": ["something"], "top_k": 3})
    finally:
        session.close()

    entry = next(f for f in result["files"] if f["filename"].endswith("many.py"))
    matched_line_numbers = {line_number for line_number,
        _text, matched in _parse_lines(entry) if matched}
    # 3 chunks merged, 2 lines each.
    assert len(matched_line_numbers) == 6
    assert result["truncated"] is True


def test_list_files_output_style_returns_deduplicated_filenames(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    _make_tree(tmp_path)
    chunks = [
        (_chunk("sub/nested.py", "function", 1, 1, "def hello():"), 0.9),
        (_chunk("sub/nested.py", "function", 2, 2, "    return 'hello again'"), 0.8),
    ]
    session, context = _context_with_session(tmp_path, make_session_config)
    session._workspace_indexer = _FakeWorkspaceIndexer(  # type: ignore[assignment]
        chunks)
    try:
        result = SemanticSearchTool(context).apply(
            {"path": "", "queries": ["something"], "outputStyle": "ListFiles"})
    finally:
        session.close()

    assert len(result["files"]) == 1
    assert result["files"][0].endswith("nested.py")


def test_invalid_output_style_raises_value_error(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session, context = _context_with_session(tmp_path, make_session_config)
    session._workspace_indexer = _FakeWorkspaceIndexer([])  # type: ignore[assignment]
    try:
        with pytest.raises(ValueError, match="outputStyle"):
            SemanticSearchTool(context).apply(
                {"path": "", "queries": ["hello"], "outputStyle": "FullContext"})
    finally:
        session.close()


def test_empty_queries_raises_value_error(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session, context = _context_with_session(tmp_path, make_session_config)
    session._workspace_indexer = _FakeWorkspaceIndexer([])  # type: ignore[assignment]
    try:
        with pytest.raises(ValueError, match="queries"):
            SemanticSearchTool(context).apply({"path": "", "queries": []})
    finally:
        session.close()
