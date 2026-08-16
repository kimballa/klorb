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
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from klorb.lockfile import Lockfile, create_lockfile
from klorb.permissions.directory_access import KLORB_PROJECT_DIR_NAME, workspace_klorb_dir
from klorb.search_index.chunk import Chunk
from klorb.search_index.chunkers.router import get_chunker_router
from klorb.search_index.embedding import embedding_model_available, get_embedding_model
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
        write_lock: WriteLock | None = None,
    ) -> None:
        self._store.delete_for_path(rel_path, write_lock)
        chunks = get_chunker_router().chunk_file(rel_path, text)
        if chunks:
            embeddings = get_embedding_model().embed_passages([chunk.text for chunk in chunks])
            self._store.upsert_chunks(chunks, embeddings, write_lock)
        self._store.set_file_hash(rel_path, content_hash, last_modified_ts, write_lock)

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
