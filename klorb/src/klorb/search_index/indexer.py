# © Copyright 2026 Aaron Kimball
"""`WorkspaceIndexer`: owns one workspace's search index, covering all catalogs. See
docs/specs/local-search-index.md.
"""

import hashlib
import logging
import threading
import time
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from klorb.lockfile import Lockfile, create_lockfile
from klorb.paths import get_klorb_data_dir
from klorb.permissions.directory_access import KLORB_PROJECT_DIR_NAME, workspace_klorb_dir
from klorb.search_index.catalogs import MEMORIES_GLOBAL_CATALOG, MEMORIES_WORKSPACE_CATALOG
from klorb.search_index.chunk import Chunk
from klorb.search_index.chunkers.base import CATALOG
from klorb.search_index.chunkers.router import get_chunker_router
from klorb.search_index.embedding import (
    EMBEDDING_THREADS,
    EmbeddingModel,
    embedding_model_available,
    get_embedding_model,
    try_gpu_embedding_model,
)
from klorb.search_index.store import FileIndexRecord, SearchIndexStore, WriteLock
from klorb.tools.memory.common import MEMORIES_DIRNAME
from klorb.tools.util.gitignore import GitignoreFilter

logger = logging.getLogger(__name__)

MAX_INDEXED_FILE_BYTES = 500_000
INDEX_DIR_NAME = "index"
DB_FILENAME = "workspace.db"
OWNER_LOCK_FILENAME = "indexer.lock"
DEBOUNCE_SECONDS = 1.0
DEFAULT_SEARCH_LIMIT = 20

_ALWAYS_SKIP_DIR_NAMES = frozenset({".git", ".svn", ".cvs", ".hg",
                                    "venv", ".venv",
                                    "node_modules", KLORB_PROJECT_DIR_NAME})

_GLOBAL_MEMORIES_VIRTUAL_DIRNAME = f"{KLORB_PROJECT_DIR_NAME}/global-memories"
"""Synthetic virtual path standing in for `KLORB_DATA_DIR/memories/` in `Chunk.source_path` and
the `files` table's bookkeeping key, since that directory isn't nested under any workspace root.
Kept under a `.klorb`-prefixed virtual path so it can never collide with a real `workspace`
catalog path, since `_walk_indexable_files` always skips `.klorb`."""


@dataclass
class ScanStats:
    """Result of `WorkspaceIndexer.run_foreground_scan()`: what changed and how long it took."""

    files_scanned: int
    files_indexed: int
    files_removed: int
    chunks_indexed: int
    elapsed_seconds: float


class _ScanPhaseTimings:
    """Cumulative wall-clock time `_scan_dirty_files` spent in each phase, summed across every
    file and worker thread -- diagnostic only, logged at `run_foreground_scan`'s debug level so a
    slow scan's dominant cost is visible directly instead of inferred from CPU usage."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.read_seconds = 0.0
        self.chunk_seconds = 0.0
        self.embed_seconds = 0.0
        self.store_seconds = 0.0

    def add_read(self, seconds: float) -> None:
        with self._lock:
            self.read_seconds += seconds

    def add_chunk(self, seconds: float) -> None:
        with self._lock:
            self.chunk_seconds += seconds

    def add_embed(self, seconds: float) -> None:
        with self._lock:
            self.embed_seconds += seconds

    def add_store(self, seconds: float) -> None:
        with self._lock:
            self.store_seconds += seconds


def _file_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _read_text(path: Path) -> str | None:
    """`path`'s decoded text, or `None` if it's unreadable, too large, or not valid UTF-8.
    """
    try:
        if path.stat().st_size > MAX_INDEXED_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _walk_indexable_files(root: Path, gitignore: GitignoreFilter) -> Iterator[Path]:
    """Depth-first walk of `root`, yielding every non-gitignored file -- skipping `.git`/`.klorb`
    unconditionally and any symlink."""
    try:
        entries = sorted(root.iterdir(), key=lambda entry: entry.name)
    except OSError:
        return
    for entry in entries:
        if entry.is_symlink():
            continue
        if entry.is_dir():
            if entry.name in _ALWAYS_SKIP_DIR_NAMES or gitignore.is_ignored(entry, is_dir=True):
                continue
            yield from _walk_indexable_files(entry, gitignore.descend(entry))
        elif entry.is_file() and not gitignore.is_ignored(entry, is_dir=False):
            yield entry


def _flat_memory_files(namespace_dir: Path) -> list[Path]:
    """Every `.md`, non-dotfile file directly in `namespace_dir`. A flat directory, so this never
    recurses and ignores gitignore."""
    try:
        entries = sorted(namespace_dir.iterdir(), key=lambda entry: entry.name)
    except OSError:
        return []
    return [
        entry for entry in entries
        if entry.is_file() and entry.suffix == ".md" and not entry.name.startswith(".")
    ]


def _workspace_file_entries(root: Path, gitignore: GitignoreFilter) -> Iterator[tuple[Path, str]]:
    for abs_path in _walk_indexable_files(root, gitignore):
        yield abs_path, abs_path.relative_to(root).as_posix()


def _workspace_memory_entries(workspace_root: Path) -> list[tuple[Path, str]]:
    """Every `.klorb/memories/` file, paired with its real workspace-root-relative path."""
    memories_dir = workspace_klorb_dir(workspace_root) / MEMORIES_DIRNAME
    return [
        (abs_path, f"{KLORB_PROJECT_DIR_NAME}/{MEMORIES_DIRNAME}/{abs_path.name}")
        for abs_path in _flat_memory_files(memories_dir)
    ]


def _global_memory_entries() -> list[tuple[Path, str]]:
    """Every global memory file, paired with a synthetic `.klorb`-rooted path."""
    memories_dir = get_klorb_data_dir() / MEMORIES_DIRNAME
    return [
        (abs_path, f"{_GLOBAL_MEMORIES_VIRTUAL_DIRNAME}/{abs_path.name}")
        for abs_path in _flat_memory_files(memories_dir)
    ]


@dataclass
class _DirtyFile:
    """One file `_scan_dirty_files` found changed or new, queued for (re)chunking/(re)embedding."""

    rel_path: str
    abs_path: Path
    mtime: float
    catalog: str


def _collect_dirty(
    entries: Iterable[tuple[Path, str]], catalog: str,
    existing: dict[str, FileIndexRecord], seen: set[str],
) -> list[_DirtyFile]:
    """Filter `entries` (all belonging to `catalog`) down to the ones whose mtime changed since
    `existing`, recording every entry's `rel_path` into `seen` along the way regardless of
    dirtiness."""
    dirty = []
    for abs_path, rel_path in entries:
        seen.add(rel_path)
        try:
            mtime = abs_path.stat().st_mtime
        except OSError:
            continue
        existing_record = existing.get(rel_path)
        if existing_record is None or existing_record.last_modified_ts != mtime:
            dirty.append(_DirtyFile(rel_path, abs_path, mtime, catalog))
    return dirty


class WorkspaceIndexer:
    """One workspace's search index. `start()` is non-blocking: it spawns a background thread
    that attempts to become the workspace's sole indexer (via a `klorb.lockfile` owner lock) and,
    if successful, runs the initial scan and starts the filesystem watcher. A process that loses
    the race stays read-only -- `hybrid_search` still works against whatever the owner process has
    already committed -- until the owner exits and this process's own next search call claims
    ownership and runs a catch-up scan.
    """

    def __init__(
        self, workspace_root: Path, *, use_gpu: bool = True, index_memories: bool = True,
    ) -> None:
        self._workspace_root = workspace_root.resolve()
        self._use_gpu = use_gpu
        self._index_memories = index_memories
        self._global_memories_dir = (get_klorb_data_dir() / MEMORIES_DIRNAME).resolve(strict=False)
        index_dir = workspace_klorb_dir(self._workspace_root) / INDEX_DIR_NAME
        index_dir.mkdir(parents=True, exist_ok=True)
        self._store = SearchIndexStore(index_dir / DB_FILENAME)
        self._owner_lock_path = index_dir / OWNER_LOCK_FILENAME
        self._owner_lock: Lockfile | None = None
        self._observer: BaseObserver | None = None
        self._timer: threading.Timer | None = None
        self._pending_paths: set[Path] = set()
        self._state_lock = threading.Lock()
        self._closing_event = threading.Event()
        self._ownership_thread: threading.Thread | None = None

    def start(self) -> None:
        """Non-blocking: spawns a background thread attempting to become this workspace's owner
        and, if successful, running the initial scan and starting the watcher."""
        thread = threading.Thread(target=self._begin_ownership, daemon=True)
        self._ownership_thread = thread
        thread.start()

    def close(self) -> None:
        """Stop this indexer.
        """

        # Signal `self._closing_event` first so an in-flight initial scan (running on the background
        # thread `start()` spawned) exits promptly between files rather than continuing to
        # completion.
        self._closing_event.set()

        if self._ownership_thread is not None:
            # Join the scanning thread so it releases use of sqlite3 db before we close it.
            self._ownership_thread.join(timeout=30)
            if self._ownership_thread.is_alive():
                logger.warning(
                    "Workspace indexer thread for %s did not stop within 30s of close(); "
                    "closing the store anyway.", self._workspace_root)
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

        # Must be done after scanning thread has been join()'d.
        # Closing the store out from under a still-running scan raises a sqlite error in that thread.
        self._store.close()
        logger.debug("Closed workspace indexer for %s.", self._workspace_root)

    def is_owner(self) -> bool:
        return self._owner_lock is not None

    def hybrid_search(
        self, query_text: str, limit: int, catalog: str = CATALOG,
    ) -> list[tuple[Chunk, float]]:
        """Fused BM25 + vector-KNN search, scoped to `catalog`, over whatever this workspace's
        index currently holds. If no process currently owns the index, this call's process
        claims ownership and starts indexing in the background, though the results returned
        *this* call are still whatever was already committed. Raises `RuntimeError` if the
        embedding model isn't available."""
        if not self.is_owner():
            self._begin_ownership()
        query_embedding = get_embedding_model(use_gpu=self._use_gpu).embed_query(query_text)
        return self._store.hybrid_search(query_text, query_embedding, limit, catalog)

    def run_foreground_scan(
        self, *, rebuild: bool = False, num_threads: int = 1, use_gpu: bool = True,
    ) -> ScanStats:
        """Synchronously scan the workspace and (re)index every dirty file, for `klorb index
        scan`. Unlike `start()`'s background catch-up scan, this blocks until finished and fans
        the chunk/embed work for changed files out across `num_threads` worker threads.

        * `rebuild` clears the index first, so every file is treated as dirty.
        * `use_gpu` (the default) tries CUDA before falling back to CPU.

        Raises `RuntimeError` if another process already owns this workspace's index (its own
        watcher already keeps it current) or if the bundled embedding model isn't installed.
        """
        if not embedding_model_available():
            raise RuntimeError("Embedding model not installed; run `klorb init` first.")
        lock = create_lockfile(self._owner_lock_path)
        if not lock.try_acquire():
            raise RuntimeError(
                f"Search index for {self._workspace_root} is owned by another running klorb "
                "process; it already keeps the index up to date.")
        self._owner_lock = lock
        try:
            with self._store.acquire_write_lock() as write_lock:
                if rebuild:
                    self._store.clear(write_lock)
                return self._scan_dirty_files(
                    write_lock, num_threads=max(1, num_threads), use_gpu=use_gpu)
        finally:
            lock.release()
            self._owner_lock = None

    def _scan_dirty_files(
        self, write_lock: WriteLock, *, num_threads: int, use_gpu: bool = True,
    ) -> ScanStats:
        """Walk the workspace and (re)index every file whose mtime or content changed since it
        was last indexed, fanning the per-file read/chunk/embed work out across `num_threads`
        worker threads when more than one is requested.

        `use_gpu` tries `try_gpu_embedding_model()` once, up front, and if that succeeds shares
        the one resulting `EmbeddingModel` across every worker thread regardless of `num_threads`.

        If `use_gpu` is false, or GPU wasn't actually available, this falls back to a CPU-only
        `EmbeddingModel` private to each worker thread (`thread_embedding_model()`,
        `_thread_local.model`, lazily built and reused for every file that thread processes).
        """
        start_time = time.monotonic()
        shared_gpu_model = try_gpu_embedding_model() if use_gpu else None
        existing = self._store.file_records()
        gitignore = GitignoreFilter.for_root(self._workspace_root, self._workspace_root)
        seen: set[str] = set()
        dirty: list[_DirtyFile] = _collect_dirty(
            _workspace_file_entries(self._workspace_root, gitignore), CATALOG, existing, seen)
        if self._index_memories:
            dirty.extend(_collect_dirty(
                _workspace_memory_entries(self._workspace_root), MEMORIES_WORKSPACE_CATALOG,
                existing, seen))
            dirty.extend(_collect_dirty(
                _global_memory_entries(), MEMORIES_GLOBAL_CATALOG, existing, seen))

        files_indexed = 0
        chunks_indexed = 0
        counts_lock = threading.Lock()
        thread_local = threading.local()
        timings = _ScanPhaseTimings()

        def thread_embedding_model() -> EmbeddingModel:
            """A CPU-only model private to the calling thread, lazily built and cached in
            `threading.local()`. When `num_threads > 1`, each thread's own session is capped to
            `threads=1` to avoid oversubscribing many concurrent sessions across worker threads.
            """
            model = getattr(thread_local, "model", None)
            if model is None:
                model = EmbeddingModel(threads=1 if num_threads > 1 else EMBEDDING_THREADS)
                thread_local.model = model
            return model

        def process_one(item: _DirtyFile) -> None:
            nonlocal files_indexed, chunks_indexed
            read_start = time.monotonic()
            text = _read_text(item.abs_path)
            timings.add_read(time.monotonic() - read_start)
            if text is None:
                return
            content_hash = _file_hash(text)
            existing_record = existing.get(item.rel_path)
            if existing_record is not None and existing_record.content_hash == content_hash:
                self._store.set_file_hash(item.rel_path, content_hash, item.mtime, write_lock)
                return
            embedding_model = shared_gpu_model if shared_gpu_model is not None \
                else thread_embedding_model()
            chunk_count = self._reindex_file(
                item.rel_path, text, content_hash, item.mtime, write_lock, embedding_model,
                timings, item.catalog)
            with counts_lock:
                files_indexed += 1
                chunks_indexed += chunk_count

        if num_threads <= 1 or len(dirty) <= 1:
            for item in dirty:
                process_one(item)
        else:
            # Submitted up front (mirroring Executor.map's own eager-submit behavior) rather than
            # via `with ThreadPoolExecutor(...) as pool:` -- that context manager's `__exit__`
            # unconditionally calls `shutdown(wait=True)`, which on an interrupt would block until
            # every already-submitted file finished, however deep the backlog, since none of them
            # get cancelled. `cancel_futures=True` here drops every not-yet-started file
            # immediately, so an interrupt only waits on the up-to-`num_threads` files already in
            # flight rather than the whole scan.
            pool = ThreadPoolExecutor(max_workers=num_threads)
            try:
                futures = [pool.submit(process_one, item) for item in dirty]
                for future in futures:
                    future.result()
            finally:
                pool.shutdown(wait=True, cancel_futures=True)

        stale_paths = set(existing) - seen
        for rel_path in stale_paths:
            self._store.delete_for_path(rel_path, write_lock)

        elapsed = time.monotonic() - start_time
        logger.debug(
            "Foreground scan of %s: %d file(s) scanned, %d indexed, %d removed, %d chunk(s) "
            "(%.1fs). Phase totals summed across %d thread(s): read=%.1fs chunk=%.1fs "
            "embed=%.1fs store=%.1fs.", self._workspace_root, len(seen), files_indexed,
            len(stale_paths), chunks_indexed, elapsed, max(1, num_threads), timings.read_seconds,
            timings.chunk_seconds, timings.embed_seconds, timings.store_seconds)
        return ScanStats(
            files_scanned=len(seen), files_indexed=files_indexed, files_removed=len(stale_paths),
            chunks_indexed=chunks_indexed, elapsed_seconds=elapsed)

    def _begin_ownership(self) -> None:
        if self.is_owner() or self._closing_event.is_set():
            return
        if not embedding_model_available():
            logger.debug(
                "Embedding model unavailable; not indexing %s (run `klorb init` first).",
                self._workspace_root)
            return
        lock = create_lockfile(self._owner_lock_path)
        if not lock.try_acquire():
            logger.debug(
                "Search index for %s already owned by another process; staying read-only.",
                self._workspace_root)
            return
        self._owner_lock = lock
        logger.debug("Claimed search index ownership for %s.", self._workspace_root)
        try:
            with self._store.acquire_write_lock() as write_lock:
                self._initial_scan(write_lock)
        except Exception:
            logger.error("Initial search index scan failed for %s.", self._workspace_root, exc_info=True)
        if not self._closing_event.is_set():
            self._start_watcher()

    def _initial_scan(self, write_lock: WriteLock) -> None:
        """Reindex every changed or new file, and drop stale ones, under a single hold of
        `write_lock` for the whole scan rather than a separate file-lock acquisition per file.
        A file whose mtime matches its stored `FileIndexRecord.last_modified_ts` is skipped
        without reading or hashing it; a file whose mtime changed but whose content hash didn't
        (e.g. a `git checkout` that touches mtimes) is skipped for chunking/embedding but still
        gets its stored mtime refreshed."""
        start_time = time.monotonic()
        existing = self._store.file_records()
        seen: set[str] = set()
        gitignore = GitignoreFilter.for_root(self._workspace_root, self._workspace_root)
        sources: list[tuple[Iterable[tuple[Path, str]], str]] = [
            (_workspace_file_entries(self._workspace_root, gitignore), CATALOG),
        ]
        if self._index_memories:
            sources.append(
                (_workspace_memory_entries(self._workspace_root), MEMORIES_WORKSPACE_CATALOG))
            sources.append((_global_memory_entries(), MEMORIES_GLOBAL_CATALOG))
        for entries, catalog in sources:
            for abs_path, rel_path in entries:
                if self._closing_event.is_set():
                    logger.debug(
                        "Initial scan of %s interrupted by close() after %d file(s).",
                        self._workspace_root, len(seen))
                    return
                seen.add(rel_path)
                self._scan_one_file(rel_path, abs_path, catalog, existing, write_lock)
        stale_paths = set(existing) - seen
        for rel_path in stale_paths:
            self._store.delete_for_path(rel_path, write_lock)
        end_time = time.monotonic()
        elapsed = end_time - start_time
        logger.debug(
            "Initial scan of %s indexed %d file(s), removed %d stale. (%.1f s)",
            self._workspace_root, len(seen), len(stale_paths), elapsed)

    def _scan_one_file(
        self, rel_path: str, abs_path: Path, catalog: str,
        existing: dict[str, FileIndexRecord], write_lock: WriteLock,
    ) -> None:
        """Reindex `abs_path` into `catalog` if its mtime or content hash changed since
        `existing`, or refresh its stored mtime if only the mtime changed."""
        try:
            mtime = abs_path.stat().st_mtime
        except OSError:
            return
        existing_record = existing.get(rel_path)
        if existing_record is not None and existing_record.last_modified_ts == mtime:
            return
        text = _read_text(abs_path)
        if text is None:
            return
        content_hash = _file_hash(text)
        if existing_record is not None and existing_record.content_hash == content_hash:
            self._store.set_file_hash(rel_path, content_hash, mtime, write_lock)
            return
        self._reindex_file(rel_path, text, content_hash, mtime, write_lock, catalog=catalog)

    def _reindex_file(
        self, rel_path: str, text: str, content_hash: str, last_modified_ts: float,
        write_lock: WriteLock | None = None, embedding_model: EmbeddingModel | None = None,
        timings: _ScanPhaseTimings | None = None, catalog: str = CATALOG,
    ) -> int:
        """(Re)chunk and (re)embed `rel_path` into `catalog`, returning how many chunks it
        produced. `embedding_model`, if given, is used instead of the shared
        `get_embedding_model()` singleton. `timings`, if given, accumulates this call's
        chunk/embed/store phase durations."""
        self._store.delete_for_path(rel_path, write_lock)
        chunk_start = time.monotonic()
        chunks = get_chunker_router().chunk_file(rel_path, text, catalog)
        if timings is not None:
            timings.add_chunk(time.monotonic() - chunk_start)
        if chunks:
            model = embedding_model if embedding_model is not None \
                else get_embedding_model(use_gpu=self._use_gpu)
            embed_start = time.monotonic()
            embeddings = model.embed_passages([chunk.text for chunk in chunks])
            if timings is not None:
                timings.add_embed(time.monotonic() - embed_start)
            store_start = time.monotonic()
            self._store.upsert_chunks(chunks, embeddings, write_lock)
            if timings is not None:
                timings.add_store(time.monotonic() - store_start)
        self._store.set_file_hash(rel_path, content_hash, last_modified_ts, write_lock)
        return len(chunks)

    def _start_watcher(self) -> None:
        if self._observer is not None or self._closing_event.is_set():
            return
        observer = Observer()
        observer.schedule(_ChangeHandler(self), str(self._workspace_root), recursive=True)
        if self._index_memories:
            # `KLORB_DATA_DIR/memories/` needs its own explicit watch, so it must exist before
            # scheduling. `.klorb/memories/` needs no such handling: it's a subdirectory of the
            # workspace root, which is already watched recursively above.
            self._global_memories_dir.mkdir(parents=True, exist_ok=True)
            observer.schedule(_ChangeHandler(self), str(self._global_memories_dir), recursive=False)
        observer.start()
        self._observer = observer
        logger.debug("Search index watcher started for %s.", self._workspace_root)

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
        if self._index_memories and abs_path.parent == self._global_memories_dir:
            rel_path = f"{_GLOBAL_MEMORIES_VIRTUAL_DIRNAME}/{abs_path.name}"
            self._reindex_changed_memory_path(abs_path, rel_path, MEMORIES_GLOBAL_CATALOG)
            return
        try:
            relative = abs_path.relative_to(self._workspace_root)
        except ValueError:
            return
        parts = relative.parts
        if (self._index_memories and len(parts) == 3
                and parts[0] == KLORB_PROJECT_DIR_NAME and parts[1] == MEMORIES_DIRNAME):
            self._reindex_changed_memory_path(
                abs_path, relative.as_posix(), MEMORIES_WORKSPACE_CATALOG)
            return
        if any(part in _ALWAYS_SKIP_DIR_NAMES for part in parts[:-1]):
            return
        rel_path = relative.as_posix()
        gitignore = GitignoreFilter.for_root(self._workspace_root, abs_path.parent)
        if not abs_path.is_file() or gitignore.is_ignored(abs_path, is_dir=False):
            self._store.delete_for_path(rel_path)
            return
        text = _read_text(abs_path)
        if text is None:
            self._store.delete_for_path(rel_path)
            return
        try:
            mtime = abs_path.stat().st_mtime
        except OSError:
            self._store.delete_for_path(rel_path)
            return
        self._reindex_file(rel_path, text, _file_hash(text), mtime, catalog=CATALOG)

    def _reindex_changed_memory_path(self, abs_path: Path, rel_path: str, catalog: str) -> None:
        """Reindex or delete a changed path in a flat memory namespace (`.klorb/memories/` or
        `KLORB_DATA_DIR/memories/`)."""
        if abs_path.name.startswith(".") or abs_path.suffix != ".md":
            return
        text = _read_text(abs_path) if abs_path.is_file() else None
        if text is None:
            self._store.delete_for_path(rel_path)
            return
        try:
            mtime = abs_path.stat().st_mtime
        except OSError:
            self._store.delete_for_path(rel_path)
            return
        self._reindex_file(rel_path, text, _file_hash(text), mtime, catalog=catalog)


class _ChangeHandler(FileSystemEventHandler):
    def __init__(self, indexer: WorkspaceIndexer) -> None:
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
