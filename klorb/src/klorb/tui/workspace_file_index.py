# © Copyright 2026 Aaron Kimball
"""Keeps a gitignore-aware list of a workspace's files fresh for the `@`-mention fuzzy file
finder (`klorb.tui.widgets.file_finder`), via filesystem push notifications from the `watchdog`
PyPI package -- unrelated to `klorb.watchdog.LivenessWatchdog`, klorb's own hang-detection
heartbeat -- rather than periodic rescans. This is the same mechanism WSGI dev-server reloaders
use to detect file changes without polling.
"""

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from klorb.tools.util.dir_walk import GIT_DIR_NAME
from klorb.tools.util.gitignore import GITIGNORE_FILENAME, GitignoreFilter

logger = logging.getLogger(__name__)

MAX_INDEXED_FILES = 20000
"""Upper bound on how many workspace files a scan enumerates, so a pathological monorepo can't
make the file finder hold an unbounded number of paths in memory."""

_DEBOUNCE_SECONDS = 0.4
"""How long `WorkspaceFileIndex` waits after the most recent filesystem event before applying
it, so a burst of events (an `npm install`, a branch checkout) collapses into one update
instead of one per event."""

_ABORT_CHECK_INTERVAL = 256
"""How many directory entries `_scan_dir` examines between each non-blocking check of the
closing signal (`WorkspaceFileIndex._closing_event.is_set()`), on top of the check it always
makes on every recursive descent into a subdirectory."""


class _ScanAborted(Exception):
    """Raised internally to unwind a rescan's recursive directory walk once `close()` has
    signaled shutdown (`WorkspaceFileIndex._closing_event`); always caught by `_rescan()` and
    never escapes it."""


def _scan_workspace_files(workspace_root: Path, should_abort: Callable[[], bool]) -> list[str]:
    """Recursively list every non-gitignored, non-`.git` file under `workspace_root` as a
    sorted, POSIX-style path relative to it, capped at `MAX_INDEXED_FILES` entries.

    `should_abort` is polled periodically (`_scan_dir`) so a caller can cancel a scan already in
    flight; raises `_ScanAborted` the moment it returns `True`, leaving the caller's own `files`
    list (if any) irrelevant.
    """
    files: list[str] = []
    _scan_dir(
        workspace_root, workspace_root, GitignoreFilter.for_root(workspace_root, workspace_root),
        files, should_abort)
    files.sort()
    return files


def _scan_dir(
    root: Path, dir_path: Path, gitignore: GitignoreFilter, files: list[str],
    should_abort: Callable[[], bool],
) -> None:
    """Recursion helper for `_scan_workspace_files`: appends `dir_path`'s own non-ignored files
    (POSIX-relative to `root`) onto `files` and recurses into its non-ignored subdirectories,
    stopping early once `MAX_INDEXED_FILES` is reached. Checks `should_abort` on every recursive
    descent into a subdirectory, and every `_ABORT_CHECK_INTERVAL` entries within one directory's
    own listing, so a long-running scan of a large tree notices `close()` promptly rather than
    only between top-level directories."""
    if should_abort():
        raise _ScanAborted()
    try:
        entries = sorted(dir_path.iterdir(), key=lambda entry: entry.name)
    except OSError:
        return
    for index, entry in enumerate(entries):
        if index % _ABORT_CHECK_INTERVAL == 0 and should_abort():
            raise _ScanAborted()
        if len(files) >= MAX_INDEXED_FILES:
            return
        if entry.is_symlink():
            # A symlinked directory is skipped outright (no cycle risk); a symlinked file is
            # indexed like any other.
            if not entry.is_dir() and not gitignore.is_ignored(entry, is_dir=False):
                files.append(entry.relative_to(root).as_posix())
            continue
        if entry.is_dir():
            if entry.name == GIT_DIR_NAME or gitignore.is_ignored(entry, is_dir=True):
                continue
            _scan_dir(root, entry, gitignore.descend(entry), files, should_abort)
        elif not gitignore.is_ignored(entry, is_dir=False):
            files.append(entry.relative_to(root).as_posix())


class _ChangeHandler(FileSystemEventHandler):
    """Routes watchdog filesystem events into `WorkspaceFileIndex`'s debounced update pipeline.

    A plain file's own creation or deletion is applied as a single incremental add/remove; a
    directory event or any change to a `.gitignore` file forces a full rescan instead, since
    either can affect more paths than the one the event names. Ordinary file content
    edits are not watched at all: they can't change
    which paths exist, and the finder only ever shows paths.
    """

    def __init__(self, index: "WorkspaceFileIndex") -> None:
        self._index = index

    def on_created(self, event: FileSystemEvent) -> None:
        self._handle(Path(str(event.src_path)), event.is_directory, created=True)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._handle(Path(str(event.src_path)), event.is_directory, created=False)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._handle(Path(str(event.src_path)), event.is_directory, created=False)
        self._handle(Path(str(event.dest_path)), event.is_directory, created=True)

    def _handle(self, path: Path, is_directory: bool, *, created: bool) -> None:
        if is_directory or path.name == GITIGNORE_FILENAME:
            self._index._queue_rescan()
        elif created:
            self._index._queue_created(path)
        else:
            self._index._queue_deleted(path)


class WorkspaceFileIndex:
    """Owns the fuzzy file finder's view of `workspace_root`'s files, kept current by a
    background `watchdog` `Observer` rather than periodic rescans.

    `start()` runs an initial synchronous scan and then watches the tree; `files` reads back a
    thread-safe snapshot at any time. `on_changed` is invoked -- from a background thread, the
    watchdog observer's or a debounce timer's -- every time `files` actually changes, so a
    caller driving a UI must marshal back onto its own thread before touching widgets from it.

    A rescan (the initial one in `start()`, or a later one `_flush()` triggers) can run on
    whatever thread invokes it and can take a while against a large tree, during which watchdog
    events keep arriving concurrently on the observer's own thread. `_rescan_in_progress`
    (guarded by `_lock`, like every other piece of mutable state here) makes that safe:
    `_queue_created`/`_queue_deleted`/`_queue_rescan` still record what happened, but skip
    scheduling a debounce timer while a rescan is running, and `_flush()` itself is a no-op if
    it fires anyway. Once a rescan
    completes, it calls `_flush()` immediately -- bypassing the debounce entirely -- to apply
    whatever queued up while it ran, so no event is ever lost to the race.

    `close()` signals `_closing_event`, which `_scan_dir` polls periodically so an in-flight
    scan aborts promptly instead of running to
    completion (or worse, restarting) after the index is supposed to be dead; it also cancels
    any pending debounce timer and stops the observer thread.
    """

    def __init__(self, workspace_root: Path, on_changed: Callable[[], None]) -> None:
        self._workspace_root = workspace_root
        self._on_changed = on_changed
        self._lock = threading.Lock()
        self._files: list[str] = []
        self._observer: BaseObserver | None = None
        self._debounce_timer: threading.Timer | None = None
        self._needs_rescan = False
        self._pending_created: set[str] = set()
        self._pending_deleted: set[str] = set()
        self._rescan_in_progress = False
        self._closing_event = threading.Event()

    @property
    def files(self) -> list[str]:
        """A thread-safe snapshot of the current, sorted file list."""
        with self._lock:
            return list(self._files)

    @property
    def is_closing(self) -> bool:
        """Whether `close()` has been called -- a non-blocking check of `_closing_event`."""
        return self._closing_event.is_set()

    def start(self) -> None:
        """Run the initial scan synchronously, then start watching `workspace_root` in the
        background. A no-op if already started or already closed."""
        if self._observer is not None or self._closing_event.is_set():
            return
        self._rescan()
        observer = Observer()
        observer.schedule(_ChangeHandler(self), str(self._workspace_root), recursive=True)
        observer.start()
        self._observer = observer
        logger.debug(
            "WorkspaceFileIndex watching %s for file/.gitignore create/delete events",
            self._workspace_root)

    def close(self) -> None:
        """Signal shutdown (notifying `_closing_event`, which an in-flight `_rescan()` polls to
        abort its walk promptly), cancel any pending debounced flush, and stop
        the background observer thread. Safe to call more than once, or when never started.
        """
        self._closing_event.set()
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None
            logger.debug("WorkspaceFileIndex stopped watching %s", self._workspace_root)

    def _rescan(self) -> None:
        """Perform a full, gitignore-aware rescan, replacing `files` with the result and
        notifying `on_changed`. Once complete, immediately (bypassing the debounce timer) calls `_flush()` to
        apply any create/delete/rescan requests that queued up while this scan was running.
        """
        with self._lock:
            if self._rescan_in_progress or self._closing_event.is_set():
                return
            self._rescan_in_progress = True
        logger.debug("WorkspaceFileIndex starting rescan of %s", self._workspace_root)
        started_at = time.monotonic()
        try:
            files = _scan_workspace_files(self._workspace_root, lambda: self._closing_event.is_set())
        except _ScanAborted:
            logger.debug(
                "WorkspaceFileIndex rescan of %s aborted by close() after %.3fs",
                self._workspace_root, time.monotonic() - started_at)
            with self._lock:
                self._rescan_in_progress = False
            return
        with self._lock:
            self._files = files
            self._rescan_in_progress = False
        if self._closing_event.is_set():
            return
        logger.debug(
            "WorkspaceFileIndex indexed %d file(s) under %s in %.3fs",
            len(files), self._workspace_root, time.monotonic() - started_at)
        self._on_changed()
        self._flush()

    def _queue_created(self, abs_path: Path) -> None:
        """Record `abs_path` (an absolute, non-directory path a watchdog event just reported as
        created) for the next flush, unless a `.gitignore` covering its own directory excludes
        it -- checked fresh via `GitignoreFilter.for_root` rather than a cached whole-tree
        filter, since only `abs_path`'s own ancestor chain of `.gitignore` files needs reading
        here, not a full rescan. Does not schedule a debounce timer while a rescan is already
        running (`_rescan_in_progress`); that rescan's own post-completion `_flush()` call picks
        this up once it finishes."""
        try:
            rel_path = abs_path.relative_to(self._workspace_root).as_posix()
        except ValueError:
            return
        gitignore = GitignoreFilter.for_root(self._workspace_root, abs_path.parent)
        if gitignore.is_ignored(abs_path, is_dir=False):
            return
        with self._lock:
            self._pending_created.add(rel_path)
            self._pending_deleted.discard(rel_path)
            rescanning = self._rescan_in_progress
        if not rescanning:
            self._schedule_flush()

    def _queue_deleted(self, abs_path: Path) -> None:
        try:
            rel_path = abs_path.relative_to(self._workspace_root).as_posix()
        except ValueError:
            return
        with self._lock:
            self._pending_deleted.add(rel_path)
            self._pending_created.discard(rel_path)
            rescanning = self._rescan_in_progress
        if not rescanning:
            self._schedule_flush()

    def _queue_rescan(self) -> None:
        with self._lock:
            self._needs_rescan = True
            rescanning = self._rescan_in_progress
        if not rescanning:
            self._schedule_flush()

    def _schedule_flush(self) -> None:
        if self._closing_event.is_set():
            return
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            timer = threading.Timer(_DEBOUNCE_SECONDS, self._flush)
            timer.daemon = True
            self._debounce_timer = timer
            timer.start()

    def _flush(self) -> None:
        """Apply whatever create/delete/rescan requests are currently pending. A no-op if a rescan is already
        running (guards the race where a timer scheduled just before one started fires while it
        is still in flight) or `close()` has already been called; either way, whatever's pending
        stays pending for a later, still-relevant call to pick up."""
        with self._lock:
            self._debounce_timer = None
            if self._rescan_in_progress or self._closing_event.is_set():
                return
            rescan = self._needs_rescan
            created = self._pending_created
            deleted = self._pending_deleted
            self._needs_rescan = False
            self._pending_created = set()
            self._pending_deleted = set()
        if rescan:
            self._rescan()
            return
        if not created and not deleted:
            return
        with self._lock:
            self._files = sorted((set(self._files) - deleted) | created)
        self._on_changed()
