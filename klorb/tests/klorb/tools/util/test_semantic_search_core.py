# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.util.semantic_search_core."""

from pathlib import Path

from klorb.search_index.chunk import Chunk, ChunkKind
from klorb.tools.util.secret_redaction import SecretRedactor
from klorb.tools.util.semantic_search_core import SemanticSearchCore


def _chunk(source_path: str, kind: ChunkKind, start_line: int, end_line: int, text: str) -> Chunk:
    return Chunk.create(
        catalog="workspace", source_path=source_path, kind=kind,
        start_line=start_line, end_line=end_line, text=text)


class _FakeIndexer:
    def __init__(self, hits: list[tuple[Chunk, float]]) -> None:
        self._hits = hits

    def hybrid_search(self, query_text: str, limit: int, catalog: str) -> list[tuple[Chunk, float]]:
        return self._hits[:limit]


def _core() -> SemanticSearchCore:
    return SemanticSearchCore(max_line_length=1000, secret_redactor=SecretRedactor())


def test_merged_hits_deduplicates_by_chunk_id_keeping_the_highest_score() -> None:
    chunk = _chunk("a.py", "function", 1, 2, "def f():\n    pass")
    indexer = _FakeIndexer([(chunk, 0.3)])
    core = _core()

    hits = core.merged_hits([(indexer, "workspace")], ["q1", "q2"], limit_per_query=10)

    assert len(hits) == 1
    assert hits[0] == (chunk, 0.3)


def test_merged_hits_keeps_the_higher_score_across_queries() -> None:
    chunk = _chunk("a.py", "function", 1, 2, "def f():\n    pass")
    core = _core()

    class _VaryingIndexer:
        def __init__(self) -> None:
            self._calls = 0

        def hybrid_search(self, query_text: str, limit: int, catalog: str) -> list[tuple[Chunk, float]]:
            self._calls += 1
            return [(chunk, 0.1 if self._calls == 1 else 0.9)]

    hits = core.merged_hits([(_VaryingIndexer(), "workspace")], ["q1", "q2"], limit_per_query=10)

    assert hits == [(chunk, 0.9)]


def test_merged_hits_merges_across_indexers() -> None:
    chunk_a = _chunk("a.py", "function", 1, 1, "a")
    chunk_b = _chunk("b.py", "function", 1, 1, "b")
    core = _core()

    hits = core.merged_hits(
        [(_FakeIndexer([(chunk_a, 0.5)]), "workspace"), (_FakeIndexer([(chunk_b, 0.4)]), "workspace")],
        ["q"], limit_per_query=10)

    assert {chunk.source_path for chunk, _score in hits} == {"a.py", "b.py"}


def test_merged_hits_drops_scores_below_min_score() -> None:
    low = _chunk("low.py", "function", 1, 1, "low")
    high = _chunk("high.py", "function", 1, 1, "high")
    core = _core()

    hits = core.merged_hits(
        [(_FakeIndexer([(low, 0.01), (high, 0.5)]), "workspace")],
        ["q"], limit_per_query=10, min_score=0.1)

    assert [chunk.source_path for chunk, _score in hits] == ["high.py"]


def test_render_chunk_lines_reads_the_current_file_content(tmp_path: Path) -> None:
    path = tmp_path / "f.py"
    path.write_text("line1\nline2\nline3\n")
    chunk = _chunk("f.py", "window", 2, 3, "stale synopsis text")
    core = _core()

    lines = core.render_chunk_lines(None, path, chunk)

    assert lines == ["*2|line2", "*3|line3"]


def test_render_chunk_lines_returns_none_for_a_missing_file(tmp_path: Path) -> None:
    chunk = _chunk("gone.py", "window", 1, 1, "text")
    core = _core()

    assert core.render_chunk_lines(None, tmp_path / "gone.py", chunk) is None


def test_render_chunk_lines_truncates_long_lines(tmp_path: Path) -> None:
    path = tmp_path / "f.py"
    path.write_text("x" * 50 + "\n")
    chunk = _chunk("f.py", "window", 1, 1, "text")
    core = SemanticSearchCore(max_line_length=10, secret_redactor=SecretRedactor())

    lines = core.render_chunk_lines(None, path, chunk)

    assert lines is not None
    assert lines[0].endswith("[truncated...]")
