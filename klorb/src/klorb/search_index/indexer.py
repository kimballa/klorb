# © Copyright 2026 Aaron Kimball
"""`WorkspaceIndexer`: owns one workspace's search index -- the initial scan, the background
filesystem watcher, and lockfile-based single-writer ownership so at most one klorb process
sharing a workspace runs the indexer at a time. See docs/specs/local-search-index.md.
"""

import hashlib
import logging
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from klorb.lockfile import Lockfile, create_lockfile
from klorb.permissions.directory_access import KLORB_PROJECT_DIR_NAME, workspace_klorb_dir
from klorb.search_index.chunk import Chunk
from klorb.search_index.chunkers.router import get_chunker_router
from klorb.search_index.embedding import EmbeddingModel, embedding_model_available, get_embedding_model
from klorb.search_index.store import SearchIndexStore, WriteLock
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


class WorkspaceIndexer:
    """One workspace's search index. `start()` is non-blocking: it spawns a background thread
    that attempts to become the workspace's sole indexer (via a `klorb.lockfile` owner lock) and,
    if successful, runs the initial scan and starts the filesystem watcher. A process that loses
    the race stays read-only -- `hybrid_search` still works against whatever the owner process has
    already committed -- until the owner exits and this process's own next search call claims
    ownership and runs a catch-up scan.
    """

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root.resolve()
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

    def hybrid_search(self, query_text: str, limit: int = DEFAULT_SEARCH_LIMIT) -> list[tuple[Chunk, float]]:
        """Fused BM25 + vector-KNN search over whatever this workspace's index currently holds.
        If no process currently owns the index, this call's process claims ownership and starts
        indexing in the background (see `start()`) -- the results returned *this* call are still
        whatever was already committed, not blocked on that catch-up scan finishing. Raises
        `RuntimeError` if the embedding model isn't available (`klorb init` hasn't run, or an
        embedding dependency failed to import)."""
        if not self.is_owner():
            self._begin_ownership()
        query_embedding = get_embedding_model().embed_query(query_text)
        return self._store.hybrid_search(query_text, query_embedding, limit)

    def run_foreground_scan(
        self, *, rebuild: bool = False, num_threads: int = 1, use_gpu: bool = False,
    ) -> ScanStats:
        """Synchronously scan the workspace and (re)index every dirty file, for `klorb index
        scan`. Unlike `start()`'s background catch-up scan, this blocks until finished and fans
        the chunk/embed work for changed files out across `num_threads` worker threads.
        `rebuild` clears the index first, so every file is treated as dirty. `use_gpu` embeds on
        CUDA instead of CPU -- see `_scan_dirty_files`'s docstring for how that changes the
        embedding-model strategy, and `EmbeddingModel` for what it requires. A `KeyboardInterrupt`
        mid-scan (`num_threads > 1`) drops every not-yet-started file immediately rather than
        draining the whole backlog, so it only waits on the up-to-`num_threads` files already in
        flight -- see `_scan_dirty_files`. Raises `RuntimeError` if another process already owns
        this workspace's index (its own watcher already keeps it current), if the bundled
        embedding model isn't installed, or if `use_gpu` is set but CUDA isn't available.
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
        self, write_lock: WriteLock, *, num_threads: int, use_gpu: bool = False,
    ) -> ScanStats:
        """Walk the workspace and (re)index every file whose mtime or content changed since it
        was last indexed, fanning the per-file read/chunk/embed work out across `num_threads`
        worker threads when more than one is requested. Used by `run_foreground_scan`;
        `_initial_scan` (the background scan `start()` runs) stays always-sequential so it can
        check `self._closing_event` between files.

        When `num_threads > 1` and `use_gpu` is false, each worker thread gets its own
        `EmbeddingModel(threads=1)` (`_thread_local.model`, lazily built and reused for every
        file that thread processes) instead of sharing `get_embedding_model()`'s cached
        singleton -- that singleton's session is capped at `EMBEDDING_THREADS` (2) for the
        *background* scan's sake, so every worker thread funneling through it would bottleneck on
        that one shared, capped session no matter how many threads are submitting work. A
        single-threaded scan keeps using the shared singleton, since there's no contention to
        avoid and no reason to load the model twice.

        `use_gpu` is the opposite shape: one `EmbeddingModel(use_gpu=True)` is built once, up
        front, and shared by every worker thread regardless of `num_threads` -- unlike CPU cores,
        one GPU is one physical device, so building `num_threads` independent CUDA sessions
        would just multiply VRAM usage and session-creation cost for no real concurrency gain.
        Raises `RuntimeError` immediately (before walking the workspace) if CUDA isn't
        available -- see `EmbeddingModel`.
        """
        start_time = time.monotonic()
        shared_gpu_model = EmbeddingModel(use_gpu=True) if use_gpu else None
        existing = self._store.file_records()
        gitignore = GitignoreFilter.for_root(self._workspace_root, self._workspace_root)
        seen: set[str] = set()
        dirty: list[tuple[str, Path, float]] = []
        for abs_path in _walk_indexable_files(self._workspace_root, gitignore):
            rel_path = abs_path.relative_to(self._workspace_root).as_posix()
            seen.add(rel_path)
            try:
                mtime = abs_path.stat().st_mtime
            except OSError:
                continue
            existing_record = existing.get(rel_path)
            if existing_record is None or existing_record.last_modified_ts != mtime:
                dirty.append((rel_path, abs_path, mtime))

        files_indexed = 0
        chunks_indexed = 0
        counts_lock = threading.Lock()
        thread_local = threading.local()
        timings = _ScanPhaseTimings()

        def thread_embedding_model() -> EmbeddingModel:
            model = getattr(thread_local, "model", None)
            if model is None:
                model = EmbeddingModel(threads=1)
                thread_local.model = model
            return model

        def process_one(item: tuple[str, Path, float]) -> None:
            nonlocal files_indexed, chunks_indexed
            rel_path, abs_path, mtime = item
            read_start = time.monotonic()
            text = _read_text(abs_path)
            timings.add_read(time.monotonic() - read_start)
            if text is None:
                return
            content_hash = _file_hash(text)
            existing_record = existing.get(rel_path)
            if existing_record is not None and existing_record.content_hash == content_hash:
                self._store.set_file_hash(rel_path, content_hash, mtime, write_lock)
                return
            if shared_gpu_model is not None:
                embedding_model = shared_gpu_model
            elif num_threads > 1:
                embedding_model = thread_embedding_model()
            else:
                embedding_model = None
            chunk_count = self._reindex_file(
                rel_path, text, content_hash, mtime, write_lock, embedding_model, timings)
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
        for abs_path in _walk_indexable_files(self._workspace_root, gitignore):
            if self._closing_event.is_set():
                logger.debug(
                    "Initial scan of %s interrupted by close() after %d file(s).",
                    self._workspace_root, len(seen))
                return
            rel_path = abs_path.relative_to(self._workspace_root).as_posix()
            seen.add(rel_path)
            try:
                mtime = abs_path.stat().st_mtime
            except OSError:
                continue
            existing_record = existing.get(rel_path)
            if existing_record is not None and existing_record.last_modified_ts == mtime:
                continue
            text = _read_text(abs_path)
            if text is None:
                continue
            content_hash = _file_hash(text)
            if existing_record is not None and existing_record.content_hash == content_hash:
                self._store.set_file_hash(rel_path, content_hash, mtime, write_lock)
                continue
            self._reindex_file(rel_path, text, content_hash, mtime, write_lock)
        stale_paths = set(existing) - seen
        for rel_path in stale_paths:
            self._store.delete_for_path(rel_path, write_lock)
        end_time = time.monotonic()
        elapsed = end_time - start_time
        logger.debug(
            "Initial scan of %s indexed %d file(s), removed %d stale. (%.1f s)",
            self._workspace_root, len(seen), len(stale_paths), elapsed)

    def _reindex_file(
        self, rel_path: str, text: str, content_hash: str, last_modified_ts: float,
        write_lock: WriteLock | None = None, embedding_model: EmbeddingModel | None = None,
        timings: _ScanPhaseTimings | None = None,
    ) -> int:
        """(Re)chunk and (re)embed `rel_path`, returning how many chunks it produced.
        `embedding_model`, if given, is used instead of the shared `get_embedding_model()`
        singleton -- see `_scan_dirty_files`. `timings`, if given, accumulates this call's
        chunk/embed/store phase durations."""
        self._store.delete_for_path(rel_path, write_lock)
        chunk_start = time.monotonic()
        chunks = get_chunker_router().chunk_file(rel_path, text)
        if timings is not None:
            timings.add_chunk(time.monotonic() - chunk_start)
        if chunks:
            model = embedding_model if embedding_model is not None else get_embedding_model()
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
        try:
            relative = abs_path.relative_to(self._workspace_root)
        except ValueError:
            return
        if any(part in _ALWAYS_SKIP_DIR_NAMES for part in relative.parts[:-1]):
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
        self._reindex_file(rel_path, text, _file_hash(text), mtime)


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
