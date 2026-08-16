# © Copyright 2026 Aaron Kimball
"""Tests for klorb.search_index.indexer."""

import os
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from klorb.search_index import indexer as indexer_module
from klorb.search_index.embedding import EMBEDDING_DIMENSIONS, EMBEDDING_THREADS
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
    def __init__(self, *, threads: int = 2, use_gpu: bool = False) -> None:
        self.threads = threads
        self.use_gpu = use_gpu

    def embed_passages(self, texts: list[str]) -> list[np.ndarray]:
        return [np.zeros(EMBEDDING_DIMENSIONS, dtype=np.float32) for _ in texts]

    def embed_query(self, text: str) -> np.ndarray:
        return np.zeros(EMBEDDING_DIMENSIONS, dtype=np.float32)


@pytest.fixture(autouse=True)
def _fake_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(indexer_module, "embedding_model_available", lambda: True)
    monkeypatch.setattr(indexer_module, "get_embedding_model", lambda: _FakeEmbeddingModel())
    monkeypatch.setattr(indexer_module, "EmbeddingModel", _FakeEmbeddingModel)
    # try_gpu_embedding_model() is a real function (not a mockable-by-proxy indirection like
    # get_embedding_model()/EmbeddingModel() above): it calls embedding.py's own EmbeddingModel
    # reference internally, unaffected by patching indexer_module.EmbeddingModel. Default it to
    # "no GPU" so run_foreground_scan()'s now-GPU-by-default tests exercise the CPU fallback path
    # deterministically, regardless of what's actually installed on the machine running the
    # suite; GPU-specific tests below override this themselves.
    monkeypatch.setattr(indexer_module, "try_gpu_embedding_model", lambda **kwargs: None)


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


def test_initial_scan_does_not_reread_a_file_whose_mtime_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "a.py").write_text("def unchanged(): pass\n")
    original_read_text = indexer_module._read_text
    read_calls: list[Path] = []

    def _tracking_read_text(path: Path) -> str | None:
        read_calls.append(path)
        return original_read_text(path)

    monkeypatch.setattr(indexer_module, "_read_text", _tracking_read_text)
    workspace_indexer = WorkspaceIndexer(tmp_path)
    try:
        with workspace_indexer._store.acquire_write_lock() as write_lock:
            workspace_indexer._initial_scan(write_lock)
        assert read_calls == [tmp_path / "a.py"]

        with workspace_indexer._store.acquire_write_lock() as write_lock:
            workspace_indexer._initial_scan(write_lock)
        assert read_calls == [tmp_path / "a.py"]  # not re-read: mtime unchanged
    finally:
        workspace_indexer.close()


def test_initial_scan_skips_reembedding_when_only_mtime_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "a.py"
    path.write_text("def unchanged(): pass\n")
    embed_calls = 0
    original_embed_passages = _FakeEmbeddingModel.embed_passages

    class _CountingEmbeddingModel(_FakeEmbeddingModel):
        def embed_passages(self, texts: list[str]) -> list[np.ndarray]:
            nonlocal embed_calls
            embed_calls += 1
            return original_embed_passages(self, texts)

    monkeypatch.setattr(indexer_module, "get_embedding_model", lambda: _CountingEmbeddingModel())
    workspace_indexer = WorkspaceIndexer(tmp_path)
    try:
        with workspace_indexer._store.acquire_write_lock() as write_lock:
            workspace_indexer._initial_scan(write_lock)
        assert embed_calls == 1

        # Rewrite the same content, bumping only the mtime -- the `git checkout` case.
        future = time.time() + 5
        path.write_text("def unchanged(): pass\n")
        os.utime(path, (future, future))

        with workspace_indexer._store.acquire_write_lock() as write_lock:
            workspace_indexer._initial_scan(write_lock)
        assert embed_calls == 1  # content hash matches -- no re-embed

        record = workspace_indexer._store.file_records()["a.py"]
        assert record.last_modified_ts == pytest.approx(future)
    finally:
        workspace_indexer.close()


def test_run_foreground_scan_indexes_dirty_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def debounce(fn):\n    return fn\n")
    (tmp_path / "b.py").write_text("def other():\n    pass\n")
    workspace_indexer = WorkspaceIndexer(tmp_path)
    try:
        stats = workspace_indexer.run_foreground_scan()

        assert stats.files_scanned == 2
        assert stats.files_indexed == 2
        assert stats.files_removed == 0
        assert stats.chunks_indexed > 0
        assert workspace_indexer._store.file_records().keys() == {"a.py", "b.py"}
    finally:
        workspace_indexer.close()


def test_run_foreground_scan_skips_files_whose_mtime_is_unchanged(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def unchanged(): pass\n")
    workspace_indexer = WorkspaceIndexer(tmp_path)
    try:
        first = workspace_indexer.run_foreground_scan()
        assert first.files_indexed == 1

        second = workspace_indexer.run_foreground_scan()
        assert second.files_indexed == 0
        assert second.files_scanned == 1
    finally:
        workspace_indexer.close()


def test_run_foreground_scan_removes_stale_files(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("def gone(): pass\n")
    workspace_indexer = WorkspaceIndexer(tmp_path)
    try:
        workspace_indexer.run_foreground_scan()
        path.unlink()

        stats = workspace_indexer.run_foreground_scan()

        assert stats.files_removed == 1
        assert workspace_indexer._store.file_records() == {}
    finally:
        workspace_indexer.close()


def test_run_foreground_scan_rebuild_reindexes_everything(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("def debounce(fn): pass\n")
    workspace_indexer = WorkspaceIndexer(tmp_path)
    try:
        workspace_indexer.run_foreground_scan()

        stats = workspace_indexer.run_foreground_scan(rebuild=True)

        assert stats.files_indexed == 1  # re-indexed despite unchanged mtime
    finally:
        workspace_indexer.close()


def test_run_foreground_scan_with_multiple_threads_indexes_every_file(tmp_path: Path) -> None:
    for i in range(10):
        (tmp_path / f"file_{i}.py").write_text(f"def fn_{i}():\n    return {i}\n")
    workspace_indexer = WorkspaceIndexer(tmp_path)
    try:
        stats = workspace_indexer.run_foreground_scan(num_threads=4)

        assert stats.files_indexed == 10
        assert len(workspace_indexer._store.file_records()) == 10
    finally:
        workspace_indexer.close()


def test_run_foreground_scan_logs_phase_timing_breakdown(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / "a.py").write_text("def debounce(fn):\n    return fn\n")
    workspace_indexer = WorkspaceIndexer(tmp_path)
    with caplog.at_level("DEBUG", logger="klorb.search_index.indexer"):
        try:
            workspace_indexer.run_foreground_scan()
        finally:
            workspace_indexer.close()

    assert "read=" in caplog.text
    assert "chunk=" in caplog.text
    assert "embed=" in caplog.text
    assert "store=" in caplog.text


def test_run_foreground_scan_multithreaded_uses_per_thread_embedding_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for i in range(12):
        (tmp_path / f"file_{i}.py").write_text(f"def fn_{i}(): return {i}\n")

    construction_threads: list[int] = []

    class _TrackingEmbeddingModel(_FakeEmbeddingModel):
        def __init__(self, *, threads: int = 2) -> None:
            construction_threads.append(threads)
            super().__init__(threads=threads)

    def _shared_model_must_not_be_used() -> _FakeEmbeddingModel:
        raise AssertionError("shared singleton must not be used during a multi-threaded scan")

    monkeypatch.setattr(indexer_module, "EmbeddingModel", _TrackingEmbeddingModel)
    monkeypatch.setattr(indexer_module, "get_embedding_model", _shared_model_must_not_be_used)

    workspace_indexer = WorkspaceIndexer(tmp_path)
    try:
        stats = workspace_indexer.run_foreground_scan(num_threads=4)

        assert stats.files_indexed == 12
        # Bounded by the worker-thread count, not one model per file -- each worker thread builds
        # its own model once and reuses it for every file that thread processes.
        assert 0 < len(construction_threads) <= 4
        assert all(threads == 1 for threads in construction_threads)
    finally:
        workspace_indexer.close()


def test_run_foreground_scan_single_threaded_cpu_fallback_never_uses_the_shared_singleton(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "a.py").write_text("def fn(): return 1\n")
    construction_threads: list[int] = []

    class _TrackingEmbeddingModel(_FakeEmbeddingModel):
        def __init__(self, *, threads: int = 2, use_gpu: bool = False) -> None:
            construction_threads.append(threads)
            super().__init__(threads=threads, use_gpu=use_gpu)

    def _shared_singleton_must_not_be_used() -> _FakeEmbeddingModel:
        raise AssertionError(
            "a --no-gpu / GPU-unavailable scan must never use get_embedding_model()'s "
            "GPU-preferring shared singleton, even when single-threaded")

    monkeypatch.setattr(indexer_module, "EmbeddingModel", _TrackingEmbeddingModel)
    monkeypatch.setattr(indexer_module, "get_embedding_model", _shared_singleton_must_not_be_used)

    workspace_indexer = WorkspaceIndexer(tmp_path)
    try:
        stats = workspace_indexer.run_foreground_scan(num_threads=1, use_gpu=False)
        assert stats.files_indexed == 1
        # A single-threaded fallback keeps the fuller EMBEDDING_THREADS pool, not threads=1 --
        # there's only one thread, so there's no oversubscription risk to guard against.
        assert construction_threads == [EMBEDDING_THREADS]
    finally:
        workspace_indexer.close()


def test_run_foreground_scan_use_gpu_builds_one_shared_model_regardless_of_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for i in range(12):
        (tmp_path / f"file_{i}.py").write_text(f"def fn_{i}(): return {i}\n")

    call_count = 0
    gpu_model = _FakeEmbeddingModel(use_gpu=True)

    def _tracking_try_gpu_embedding_model(**kwargs: object) -> _FakeEmbeddingModel:
        nonlocal call_count
        call_count += 1
        return gpu_model

    def _shared_singleton_must_not_be_used() -> _FakeEmbeddingModel:
        raise AssertionError("a GPU scan must not fall back to the shared CPU singleton")

    def _per_thread_model_must_not_be_constructed(*, threads: int = 2) -> _FakeEmbeddingModel:
        raise AssertionError("a GPU scan must not build per-thread CPU models either")

    monkeypatch.setattr(indexer_module, "try_gpu_embedding_model", _tracking_try_gpu_embedding_model)
    monkeypatch.setattr(indexer_module, "get_embedding_model", _shared_singleton_must_not_be_used)
    monkeypatch.setattr(indexer_module, "EmbeddingModel", _per_thread_model_must_not_be_constructed)

    workspace_indexer = WorkspaceIndexer(tmp_path)
    try:
        stats = workspace_indexer.run_foreground_scan(num_threads=4, use_gpu=True)

        assert stats.files_indexed == 12
        # try_gpu_embedding_model() called exactly once, up front -- not once per worker thread
        # the way the CPU per-thread-model path builds its models, since a GPU is one physical
        # device.
        assert call_count == 1
    finally:
        workspace_indexer.close()


def test_run_foreground_scan_falls_back_to_cpu_when_gpu_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "a.py").write_text("def fn(): return 1\n")
    monkeypatch.setattr(indexer_module, "try_gpu_embedding_model", lambda **kwargs: None)

    workspace_indexer = WorkspaceIndexer(tmp_path)
    try:
        stats = workspace_indexer.run_foreground_scan(num_threads=1, use_gpu=True)
        assert stats.files_indexed == 1  # falls back to CPU silently rather than raising
    finally:
        workspace_indexer.close()


def test_run_foreground_scan_no_gpu_skips_the_gpu_attempt_entirely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "a.py").write_text("def fn(): return 1\n")

    def _gpu_attempt_must_not_happen(**kwargs: object) -> _FakeEmbeddingModel:
        raise AssertionError("use_gpu=False must not even try try_gpu_embedding_model()")

    monkeypatch.setattr(indexer_module, "try_gpu_embedding_model", _gpu_attempt_must_not_happen)

    workspace_indexer = WorkspaceIndexer(tmp_path)
    try:
        stats = workspace_indexer.run_foreground_scan(num_threads=1, use_gpu=False)
        assert stats.files_indexed == 1
    finally:
        workspace_indexer.close()


def test_run_foreground_scan_cancels_pending_work_on_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for i in range(20):
        (tmp_path / f"file_{i}.py").write_text(f"def fn_{i}(): return {i}\n")

    processed_count = 0
    original_embed_passages = _FakeEmbeddingModel.embed_passages

    class _InterruptingEmbeddingModel(_FakeEmbeddingModel):
        def embed_passages(self, texts: list[str]) -> list[np.ndarray]:
            nonlocal processed_count
            processed_count += 1
            if processed_count == 2:
                raise KeyboardInterrupt()
            return original_embed_passages(self, texts)

    monkeypatch.setattr(indexer_module, "EmbeddingModel", _InterruptingEmbeddingModel)
    workspace_indexer = WorkspaceIndexer(tmp_path)
    try:
        with pytest.raises(KeyboardInterrupt):
            workspace_indexer.run_foreground_scan(num_threads=2)

        # If shutdown() had drained the whole backlog instead of cancelling it (the bug under
        # test), every one of the 20 dirty files would still get indexed despite the interrupt.
        assert len(workspace_indexer._store.file_records()) < 20
    finally:
        workspace_indexer.close()


def test_run_foreground_scan_raises_without_an_embedding_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(indexer_module, "embedding_model_available", lambda: False)
    workspace_indexer = WorkspaceIndexer(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="Embedding model"):
            workspace_indexer.run_foreground_scan()
    finally:
        workspace_indexer.close()


def test_run_foreground_scan_raises_when_another_process_owns_the_index(tmp_path: Path) -> None:
    owner = WorkspaceIndexer(tmp_path)
    contender = WorkspaceIndexer(tmp_path)
    try:
        owner.start()
        assert _wait_until(owner.is_owner)

        with pytest.raises(RuntimeError, match="owned by another running klorb process"):
            contender.run_foreground_scan()
    finally:
        owner.close()
        contender.close()


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
