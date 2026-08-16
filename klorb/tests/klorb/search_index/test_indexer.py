# © Copyright 2026 Aaron Kimball
"""Tests for klorb.search_index.indexer."""

import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from klorb.search_index import indexer as indexer_module
from klorb.search_index.embedding import EMBEDDING_DIMENSIONS
from klorb.search_index.indexer import WorkspaceIndexer, _file_hash, _read_text, _walk_indexable_files
from klorb.tools.util.gitignore import GitignoreFilter


def _wait_until(predicate: Callable[[], bool], *, timeout: float = 5.0) -> bool:
    """Poll `predicate` until it's true or `timeout` seconds elapse -- there's no async pilot to
    drive here (`WorkspaceIndexer` does its work on a real background thread), so a short
    synchronous poll loop is the direct equivalent."""
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
    monkeypatch.setattr(indexer_module, "embedding_model_available", lambda: True)
    monkeypatch.setattr(indexer_module, "get_embedding_model", lambda: _FakeEmbeddingModel())


def test_file_hash_is_stable_for_the_same_text() -> None:
    assert _file_hash("hello") == _file_hash("hello")
    assert _file_hash("hello") != _file_hash("world")


def test_read_text_returns_none_for_a_missing_file(tmp_path: Path) -> None:
    assert _read_text(tmp_path / "missing.txt") is None


def test_read_text_returns_none_for_an_oversized_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(indexer_module, "MAX_INDEXED_FILE_BYTES", 4)
    path = tmp_path / "big.txt"
    path.write_text("this is more than four bytes")

    assert _read_text(path) is None


def test_walk_indexable_files_skips_git_and_klorb_dirs(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print(1)\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("git internals\n")
    (tmp_path / ".klorb").mkdir()
    (tmp_path / ".klorb" / "index.db").write_text("not real\n")

    gitignore = GitignoreFilter.for_root(tmp_path, tmp_path)
    found = {path.relative_to(tmp_path).as_posix()
             for path in _walk_indexable_files(tmp_path, gitignore)}

    assert found == {"a.py"}


def test_walk_indexable_files_respects_gitignore(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print(1)\n")
    (tmp_path / "b.log").write_text("noise\n")
    (tmp_path / ".gitignore").write_text("*.log\n")

    gitignore = GitignoreFilter.for_root(tmp_path, tmp_path)
    found = {path.relative_to(tmp_path).as_posix()
             for path in _walk_indexable_files(tmp_path, gitignore)}

    assert found == {"a.py", ".gitignore"}


def test_start_becomes_owner_and_indexes_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def debounce(fn):\n    return fn\n")
    workspace_indexer = WorkspaceIndexer(tmp_path)
    try:
        workspace_indexer.start()
        assert _wait_until(workspace_indexer.is_owner)

        results = _wait_until(lambda: len(workspace_indexer.hybrid_search("debounce", 10)) > 0)
        assert results
    finally:
        workspace_indexer.close()


def test_close_during_an_in_flight_scan_does_not_log_a_scan_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Regression test: `close()` used to close the store immediately, while `start()`'s
    background thread was still mid-scan; the scan's next store call then raised
    `pysqlite3.dbapi2.ProgrammingError`, caught by `_begin_ownership`'s own handler and logged as
    "Initial search index scan failed" -- a real bug (an abandoned scan closed out from under
    itself), just not a process crash, since that handler already existed. `close()` must signal
    and join the background thread before touching the store, so closing never races a still-
    running scan's own store calls."""
    for i in range(30):
        (tmp_path / f"file_{i}.py").write_text(f"def fn_{i}():\n    return {i}\n")

    class _SlowEmbeddingModel(_FakeEmbeddingModel):
        def embed_passages(self, texts: list[str]) -> list[np.ndarray]:
            time.sleep(0.3)
            return super().embed_passages(texts)

    monkeypatch.setattr(indexer_module, "get_embedding_model", lambda: _SlowEmbeddingModel())

    workspace_indexer = WorkspaceIndexer(tmp_path)
    with caplog.at_level("ERROR", logger="klorb.search_index.indexer"):
        workspace_indexer.start()
        assert _wait_until(workspace_indexer.is_owner)
        workspace_indexer.close()  # closes while the 30-file scan is still very likely in flight
        # close() itself may not wait for the background thread (that's the bug under test), so
        # give it a beat to reach its next store call -- the same shape as _wait_until, just
        # polling for the log line to (not) appear rather than a state predicate.
        time.sleep(1.0)

    assert "Initial search index scan failed" not in caplog.text


def test_second_indexer_stays_read_only_while_the_first_owns_it(tmp_path: Path) -> None:
    first = WorkspaceIndexer(tmp_path)
    second = WorkspaceIndexer(tmp_path)
    try:
        first.start()
        assert _wait_until(first.is_owner)

        second.start()
        time.sleep(0.2)  # give a wrongly-eager second indexer a chance to (incorrectly) claim it
        assert not second.is_owner()
    finally:
        first.close()
        second.close()


def test_ownership_transfers_after_the_owner_closes(tmp_path: Path) -> None:
    first = WorkspaceIndexer(tmp_path)
    second = WorkspaceIndexer(tmp_path)
    try:
        first.start()
        assert _wait_until(first.is_owner)
        first.close()

        second.hybrid_search("anything", 10)  # triggers a claim attempt if not already owner
        assert _wait_until(second.is_owner)
    finally:
        second.close()


def test_hybrid_search_raises_without_an_embedding_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _unavailable() -> "_FakeEmbeddingModel":
        raise FileNotFoundError("no model")

    monkeypatch.setattr(indexer_module, "get_embedding_model", _unavailable)
    workspace_indexer = WorkspaceIndexer(tmp_path)
    try:
        with pytest.raises(FileNotFoundError):
            workspace_indexer.hybrid_search("anything", 10)
    finally:
        workspace_indexer.close()
