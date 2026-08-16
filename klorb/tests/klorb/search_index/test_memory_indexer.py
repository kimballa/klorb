# © Copyright 2026 Aaron Kimball
"""Tests for klorb.search_index.memory_indexer."""

import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from klorb.search_index import memory_indexer as memory_indexer_module
from klorb.search_index.catalogs import MEMORIES_GLOBAL_CATALOG, MEMORIES_WORKSPACE_CATALOG
from klorb.search_index.embedding import EMBEDDING_DIMENSIONS
from klorb.search_index.memory_indexer import MemoryCatalogIndexer, get_global_memory_indexer


def _wait_until(predicate: Callable[[], bool], *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


class _FakeEmbeddingModel:
    def embed_passages(self, texts: list[str]) -> list[np.ndarray]:
        return [np.zeros(EMBEDDING_DIMENSIONS, dtype=np.float32) for _ in texts]

    def embed_query(self, text: str) -> np.ndarray:
        return np.zeros(EMBEDDING_DIMENSIONS, dtype=np.float32)


@pytest.fixture(autouse=True)
def _fake_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory_indexer_module, "embedding_model_available", lambda: True)
    monkeypatch.setattr(memory_indexer_module, "get_embedding_model", lambda: _FakeEmbeddingModel())


def test_start_becomes_owner_and_indexes_memory_files(tmp_path: Path) -> None:
    namespace_dir = tmp_path / "memories"
    namespace_dir.mkdir()
    (namespace_dir / "notes.md").write_text("Topic\nA note about debouncing input.\n")
    indexer = MemoryCatalogIndexer(namespace_dir, MEMORIES_GLOBAL_CATALOG, tmp_path / "index")
    try:
        indexer.start()
        assert _wait_until(indexer.is_owner)

        results = _wait_until(lambda: len(indexer.hybrid_search("debouncing", 10)) > 0)
        assert results
    finally:
        indexer.close()


def test_ignores_non_markdown_and_dotfiles(tmp_path: Path) -> None:
    namespace_dir = tmp_path / "memories"
    namespace_dir.mkdir()
    (namespace_dir / "notes.md").write_text("Topic\nreal memory\n")
    (namespace_dir / "notes.txt").write_text("not markdown\n")
    (namespace_dir / ".hidden.md").write_text("hidden\n")
    indexer = MemoryCatalogIndexer(namespace_dir, MEMORIES_GLOBAL_CATALOG, tmp_path / "index")
    try:
        indexer.start()
        assert _wait_until(indexer.is_owner)
        assert _wait_until(lambda: len(indexer.hybrid_search("memory", 10)) > 0)

        hits = indexer.hybrid_search("markdown", 10)
        assert all(chunk.source_path == "notes.md" for chunk, _score in hits)
    finally:
        indexer.close()


def test_watcher_picks_up_a_newly_created_file(tmp_path: Path) -> None:
    namespace_dir = tmp_path / "memories"
    namespace_dir.mkdir()
    indexer = MemoryCatalogIndexer(namespace_dir, MEMORIES_GLOBAL_CATALOG, tmp_path / "index")
    try:
        indexer.start()
        assert _wait_until(indexer.is_owner)

        (namespace_dir / "new.md").write_text("Topic\na freshly created memory\n")

        assert _wait_until(
            lambda: len(indexer.hybrid_search("freshly created", 10)) > 0, timeout=5.0)
    finally:
        indexer.close()


def test_two_catalogs_get_separate_databases(tmp_path: Path) -> None:
    index_dir = tmp_path / "index"
    global_dir = tmp_path / "global-memories"
    global_dir.mkdir()
    workspace_dir = tmp_path / "workspace-memories"
    workspace_dir.mkdir()
    global_indexer = MemoryCatalogIndexer(global_dir, MEMORIES_GLOBAL_CATALOG, index_dir)
    workspace_indexer = MemoryCatalogIndexer(workspace_dir, MEMORIES_WORKSPACE_CATALOG, index_dir)
    try:
        assert global_indexer._store._db_path != workspace_indexer._store._db_path
    finally:
        global_indexer.close()
        workspace_indexer.close()


def test_get_global_memory_indexer_returns_the_same_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "klorb.paths.get_klorb_data_dir", lambda: tmp_path / "data")
    first = get_global_memory_indexer()
    second = get_global_memory_indexer()
    try:
        assert first is second
    finally:
        assert first is not None
        first.close()
        memory_indexer_module.reset_global_memory_indexer_for_testing()


def test_get_global_memory_indexer_returns_none_when_embedding_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_indexer_module, "embedding_model_available", lambda: False)

    assert get_global_memory_indexer() is None
