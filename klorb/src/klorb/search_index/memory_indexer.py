# © Copyright 2026 Aaron Kimball
"""`MemoryCatalogIndexer`: owns one memory namespace's search index. See
docs/specs/local-search-index.md.
"""

import hashlib
import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from klorb.lockfile import Lockfile, create_lockfile
from klorb.search_index.chunk import Chunk
from klorb.search_index.chunkers.router import get_chunker_router
from klorb.search_index.embedding import EmbeddingModel, embedding_model_available, get_embedding_model
from klorb.search_index.indexer import DEBOUNCE_SECONDS, DEFAULT_SEARCH_LIMIT, MAX_INDEXED_FILE_BYTES
from klorb.search_index.store import SearchIndexStore, WriteLock

logger = logging.getLogger(__name__)

MEMORIES_GLOBAL_CATALOG = "memories-global"
MEMORIES_WORKSPACE_CATALOG = "memories-workspace"


def namespace_for_catalog(catalog: str) -> str:
    """Return the memory namespace (`"global"`/`"workspace"`) `catalog` belongs to. Raises
    `ValueError` if `catalog` isn't one of the two memories catalogs."""
    if catalog == MEMORIES_GLOBAL_CATALOG:
        return "global"
    if catalog == MEMORIES_WORKSPACE_CATALOG:
        return "workspace"
    raise ValueError(f"{catalog!r} is not a memories catalog")


def _file_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _read_text(path: Path) -> str | None:
    """`path`'s decoded text, or `None` if it's unreadable, too large, or not valid UTF-8."""
    try:
        if path.stat().st_size > MAX_INDEXED_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _memory_files(namespace_dir: Path) -> list[Path]:
    """Every `.md`, non-dotfile file directly in `namespace_dir`."""
    try:
        entries = sorted(namespace_dir.iterdir(), key=lambda entry: entry.name)
    except OSError:
        return []
    return [
        entry for entry in entries
        if entry.is_file() and entry.suffix == ".md" and not entry.name.startswith(".")
    ]


class MemoryCatalogIndexer:
    """One memory namespace's search index over a flat, `.md`-only `namespace_dir`, indexed into
    `catalog` and stored at `${index_dir}/{catalog}.db`."""

    def __init__(self, namespace_dir: Path, catalog: str, index_dir: Path) -> None:
        self._namespace_dir = namespace_dir
        self._catalog = catalog
        index_dir.mkdir(parents=True, exist_ok=True)
        self._store = SearchIndexStore(index_dir / f"{catalog}.db")
        self._owner_lock_path = index_dir / f"{catalog}.lock"
        self._owner_lock: Lockfile | None = None
        self._observer: BaseObserver | None = None
        self._timer: threading.Timer | None = None
        self._pending_paths: set[Path] = set()
        self._state_lock = threading.Lock()
        self._closing_event = threading.Event()
        self._ownership_thread: threading.Thread | None = None

    def start(self) -> None:
        """Non-blocking: spawns a background thread attempting to become this namespace's owner
        and, if successful, running the initial scan and starting the watcher."""
        thread = threading.Thread(target=self._begin_ownership, daemon=True)
        self._ownership_thread = thread
        thread.start()

    def close(self) -> None:
        """Stop this indexer."""
        self._closing_event.set()
        if self._ownership_thread is not None:
            self._ownership_thread.join(timeout=30)
            if self._ownership_thread.is_alive():
                logger.warning(
                    "Memory catalog indexer thread for %s did not stop within 30s of close(); "
                    "closing the store anyway.", self._namespace_dir)
            self._ownership_thread = None
        with self._state_lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None
        if self._owner_lock is not None:
            self._owner_lock.release()
            self._owner_lock = None
        self._store.close()
        logger.debug("Closed memory catalog indexer for %s.", self._namespace_dir)

    def is_owner(self) -> bool:
        return self._owner_lock is not None

    def hybrid_search(
        self, query_text: str, limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> list[tuple[Chunk, float]]:
        """Fused BM25 + vector-KNN search over whatever this namespace's index currently holds."""
        if not self.is_owner():
            self._begin_ownership()
        query_embedding = get_embedding_model().embed_query(query_text)
        return self._store.hybrid_search(query_text, query_embedding, limit)

    def _begin_ownership(self) -> None:
        if self.is_owner() or self._closing_event.is_set():
            return
        if not embedding_model_available():
            logger.debug(
                "Embedding model unavailable; not indexing %s (run `klorb init` first).",
                self._namespace_dir)
            return
        lock = create_lockfile(self._owner_lock_path)
        if not lock.try_acquire():
            logger.debug(
                "Memory catalog index for %s already owned by another process; staying "
                "read-only.", self._namespace_dir)
            return
        self._owner_lock = lock
        logger.debug("Claimed memory catalog index ownership for %s.", self._namespace_dir)
        try:
            # A memory namespace directory isn't created eagerly, but watchdog requires the
            # watched directory to exist, so the indexer creates it itself.
            self._namespace_dir.mkdir(parents=True, exist_ok=True)
            with self._store.acquire_write_lock() as write_lock:
                self._initial_scan(write_lock)
        except Exception:
            logger.error(
                "Initial memory catalog index scan failed for %s.", self._namespace_dir,
                exc_info=True)
        if not self._closing_event.is_set():
            self._start_watcher()

    def _initial_scan(self, write_lock: WriteLock) -> None:
        start_time = time.monotonic()
        existing = self._store.file_records()
        seen: set[str] = set()
        for abs_path in _memory_files(self._namespace_dir):
            if self._closing_event.is_set():
                logger.debug(
                    "Initial scan of %s interrupted by close() after %d file(s).",
                    self._namespace_dir, len(seen))
                return
            filename = abs_path.name
            seen.add(filename)
            try:
                mtime = abs_path.stat().st_mtime
            except OSError:
                continue
            existing_record = existing.get(filename)
            if existing_record is not None and existing_record.last_modified_ts == mtime:
                continue
            text = _read_text(abs_path)
            if text is None:
                continue
            content_hash = _file_hash(text)
            if existing_record is not None and existing_record.content_hash == content_hash:
                self._store.set_file_hash(filename, content_hash, mtime, write_lock)
                continue
            self._reindex_file(filename, text, content_hash, mtime, write_lock)
        stale_filenames = set(existing) - seen
        for filename in stale_filenames:
            self._store.delete_for_path(filename, write_lock)
        elapsed = time.monotonic() - start_time
        logger.debug(
            "Initial scan of %s indexed %d file(s), removed %d stale. (%.1f s)",
            self._namespace_dir, len(seen), len(stale_filenames), elapsed)

    def _reindex_file(
        self, filename: str, text: str, content_hash: str, last_modified_ts: float,
        write_lock: WriteLock | None = None,
    ) -> int:
        self._store.delete_for_path(filename, write_lock)
        chunks = get_chunker_router().chunk_file(filename, text, self._catalog)
        if chunks:
            model: EmbeddingModel = get_embedding_model()
            embeddings = model.embed_passages([chunk.text for chunk in chunks])
            self._store.upsert_chunks(chunks, embeddings, write_lock)
        self._store.set_file_hash(filename, content_hash, last_modified_ts, write_lock)
        return len(chunks)

    def _start_watcher(self) -> None:
        if self._observer is not None or self._closing_event.is_set():
            return
        observer = Observer()
        observer.schedule(_ChangeHandler(self), str(self._namespace_dir), recursive=False)
        observer.start()
        self._observer = observer
        logger.debug("Memory catalog index watcher started for %s.", self._namespace_dir)

    def _record_change(self, abs_path: Path) -> None:
        with self._state_lock:
            self._pending_paths.add(abs_path)
            if self._timer is not None:
                self._timer.cancel()
            timer = threading.Timer(DEBOUNCE_SECONDS, self._flush_pending_changes)
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _flush_pending_changes(self) -> None:
        with self._state_lock:
            pending = self._pending_paths
            self._pending_paths = set()
            self._timer = None
        if not pending or self._closing_event.is_set():
            return
        for abs_path in pending:
            self._reindex_changed_path(abs_path)

    def _reindex_changed_path(self, abs_path: Path) -> None:
        if abs_path.parent != self._namespace_dir:
            return
        filename = abs_path.name
        if filename.startswith(".") or abs_path.suffix != ".md":
            return
        text = _read_text(abs_path) if abs_path.is_file() else None
        if text is None:
            self._store.delete_for_path(filename)
            return
        try:
            mtime = abs_path.stat().st_mtime
        except OSError:
            self._store.delete_for_path(filename)
            return
        self._reindex_file(filename, text, _file_hash(text), mtime)


class _ChangeHandler(FileSystemEventHandler):
    def __init__(self, indexer: MemoryCatalogIndexer) -> None:
        self._indexer = indexer

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._indexer._record_change(Path(str(event.src_path)))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._indexer._record_change(Path(str(event.src_path)))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._indexer._record_change(Path(str(event.src_path)))

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._indexer._record_change(Path(str(event.src_path)))
        self._indexer._record_change(Path(str(event.dest_path)))


_global_memory_indexer: MemoryCatalogIndexer | None = None
_global_memory_indexer_lock = threading.Lock()


def get_global_memory_indexer() -> "MemoryCatalogIndexer | None":
    """Return the process-wide `MemoryCatalogIndexer` for the `memories-global` catalog,
    constructing and starting it on first call, or `None` if the embedding model isn't
    installed."""
    global _global_memory_indexer
    with _global_memory_indexer_lock:
        if _global_memory_indexer is not None:
            return _global_memory_indexer
        if not embedding_model_available():
            return None
        from klorb.paths import get_klorb_data_dir
        from klorb.tools.memory.common import MEMORIES_DIRNAME
        namespace_dir = get_klorb_data_dir() / MEMORIES_DIRNAME
        index_dir = get_klorb_data_dir() / "index"
        indexer = MemoryCatalogIndexer(namespace_dir, MEMORIES_GLOBAL_CATALOG, index_dir)
        indexer.start()
        _global_memory_indexer = indexer
        return _global_memory_indexer


def reset_global_memory_indexer_for_testing() -> None:
    """Drop the process-wide singleton without closing it, so the next `get_global_memory_indexer()`
    call constructs a fresh one. Test isolation only, never called from production code."""
    global _global_memory_indexer
    with _global_memory_indexer_lock:
        _global_memory_indexer = None
