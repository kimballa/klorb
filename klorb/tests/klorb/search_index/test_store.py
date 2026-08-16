# © Copyright 2026 Aaron Kimball
"""Tests for klorb.search_index.store."""

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from klorb.search_index import store as store_module
from klorb.search_index.chunk import Chunk
from klorb.search_index.store import FileIndexRecord, SearchIndexStore


def _chunk(source_path: str, text: str, *, start_line: int = 1, end_line: int = 1) -> Chunk:
    return Chunk.create(
        catalog="workspace", source_path=source_path, kind="toplevel",
        start_line=start_line, end_line=end_line, text=text)


def _vector(seed: int) -> np.ndarray:
    return np.random.RandomState(seed).rand(4).astype(np.float32)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SearchIndexStore]:
    monkeypatch.setattr(store_module, "EMBEDDING_DIMENSIONS", 4)
    result = SearchIndexStore(tmp_path / "index.db")
    yield result
    result.close()


def test_upsert_and_search_lexical_finds_matching_text(store: SearchIndexStore) -> None:
    chunk = _chunk("a.py", "def debounce(fn): pass")
    store.upsert_chunks([chunk], [_vector(1)])

    assert store.search_lexical("debounce", 10) == [chunk.chunk_id]


def test_search_lexical_finds_nothing_for_an_absent_term(store: SearchIndexStore) -> None:
    chunk = _chunk("a.py", "def debounce(fn): pass")
    store.upsert_chunks([chunk], [_vector(1)])

    assert store.search_lexical("nonexistent_term_xyz", 10) == []


def test_search_vector_ranks_the_nearest_embedding_first(store: SearchIndexStore) -> None:
    near = _chunk("near.py", "near chunk", start_line=1, end_line=1)
    far = _chunk("far.py", "far chunk", start_line=1, end_line=1)
    query_vector = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    store.upsert_chunks(
        [near, far],
        [np.array([0.9, 0.1, 0.0, 0.0], dtype=np.float32), np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)])

    results = store.search_vector(query_vector, 10)

    assert results[0] == near.chunk_id


def test_hybrid_search_returns_chunks_with_scores(store: SearchIndexStore) -> None:
    chunk = _chunk("a.py", "def debounce(fn): pass")
    store.upsert_chunks([chunk], [_vector(1)])

    results = store.hybrid_search("debounce", _vector(1), 10)

    assert len(results) == 1
    result_chunk, score = results[0]
    assert result_chunk.chunk_id == chunk.chunk_id
    assert score > 0


def test_upsert_is_idempotent_for_the_same_chunk_id(store: SearchIndexStore) -> None:
    original = _chunk("a.py", "def debounce(fn): pass")
    store.upsert_chunks([original], [_vector(1)])
    updated = _chunk("a.py", "def debounce(fn): return fn")  # same span, new text
    store.upsert_chunks([updated], [_vector(2)])

    assert store.search_lexical("debounce", 10) == [updated.chunk_id]
    loaded = store.hybrid_search("debounce", _vector(2), 10)
    assert loaded[0][0].text == "def debounce(fn): return fn"


def test_delete_for_path_removes_every_chunk_for_that_file(store: SearchIndexStore) -> None:
    chunk_a = _chunk("a.py", "def debounce(fn): pass", start_line=1, end_line=1)
    chunk_b = _chunk("a.py", "def other(): pass", start_line=2, end_line=2)
    other_file = _chunk("b.py", "def debounce(fn): pass")
    store.upsert_chunks([chunk_a, chunk_b, other_file], [_vector(1), _vector(2), _vector(3)])

    store.delete_for_path("a.py")

    assert store.search_lexical("debounce", 10) == [other_file.chunk_id]


def test_file_records_round_trip(store: SearchIndexStore) -> None:
    store.set_file_hash("a.py", "hash1", 100.0)
    store.set_file_hash("b.py", "hash2", 200.0)

    assert store.file_records() == {
        "a.py": FileIndexRecord(content_hash="hash1", last_modified_ts=100.0),
        "b.py": FileIndexRecord(content_hash="hash2", last_modified_ts=200.0),
    }


def test_set_file_hash_overwrites_an_existing_value(store: SearchIndexStore) -> None:
    store.set_file_hash("a.py", "hash1", 100.0)
    store.set_file_hash("a.py", "hash2", 200.0)

    assert store.file_records() == {
        "a.py": FileIndexRecord(content_hash="hash2", last_modified_ts=200.0),
    }


def test_delete_for_path_removes_its_file_hash(store: SearchIndexStore) -> None:
    store.set_file_hash("a.py", "hash1", 100.0)

    store.delete_for_path("a.py")

    assert store.file_records() == {}


def test_upsert_chunks_rejects_mismatched_lengths(store: SearchIndexStore) -> None:
    chunk = _chunk("a.py", "def debounce(fn): pass")

    with pytest.raises(ValueError, match="length"):
        store.upsert_chunks([chunk], [])


def test_clear_removes_every_chunk_and_file_record(store: SearchIndexStore) -> None:
    store.upsert_chunks([_chunk("a.py", "def debounce(fn): pass")], [_vector(1)])
    store.set_file_hash("a.py", "hash1", 100.0)

    store.clear()

    assert store.search_lexical("debounce", 10) == []
    assert store.file_records() == {}


def test_stats_counts_files_and_chunks_by_kind(store: SearchIndexStore) -> None:
    class_chunk = Chunk.create(
        catalog="workspace", source_path="a.py", kind="class",
        start_line=1, end_line=5, text="class A: pass")
    method_chunk = Chunk.create(
        catalog="workspace", source_path="a.py", kind="method",
        start_line=2, end_line=3, text="def m(self): pass")
    store.upsert_chunks([class_chunk, method_chunk], [_vector(1), _vector(2)])
    store.set_file_hash("a.py", "hash1", 100.0)

    stats = store.stats()

    assert stats.file_count == 1
    assert stats.chunk_count == 2
    assert stats.chunk_counts_by_kind == {"class": 1, "method": 1}
    assert stats.db_size_bytes > 0


def test_stats_on_an_empty_store(store: SearchIndexStore) -> None:
    stats = store.stats()

    assert stats.file_count == 0
    assert stats.chunk_count == 0
    assert stats.chunk_counts_by_kind == {}


def test_reopening_with_a_stale_schema_version_rebuilds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(store_module, "EMBEDDING_DIMENSIONS", 4)
    db_path = tmp_path / "index.db"
    first = SearchIndexStore(db_path)
    first.upsert_chunks([_chunk("a.py", "def debounce(fn): pass")], [_vector(1)])
    first.close()

    monkeypatch.setattr(store_module, "SCHEMA_VERSION", "999.0.0")
    second = SearchIndexStore(db_path)
    try:
        assert second.search_lexical("debounce", 10) == []
    finally:
        second.close()
