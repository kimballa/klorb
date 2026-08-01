# © Copyright 2026 Aaron Kimball
"""Keeps a gitignore-aware list of a workspace's files fresh for the `@`-mention fuzzy file
finder (`klorb.tui.widgets.file_finder`), via filesystem push notifications from the `watchdog`
PyPI package -- unrelated to `klorb.watchdog.LivenessWatchdog`, klorb's own hang-detection
heartbeat -- rather than periodic rescans. This is the same mechanism WSGI dev-server reloaders
(e.g. werkzeug's) use to detect file changes without polling.
"""

import logging
import threading
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


def _scan_workspace_files(workspace_root: Path) -> list[str]:
    """Recursively list every non-gitignored, non-`.git` file under `workspace_root` as a
    sorted, POSIX-style path relative to it, capped at `MAX_INDEXED_FILES` entries."""
    files: list[str] = []
    _scan_dir(workspace_root, workspace_root, GitignoreFilter.for_root(workspace_root, workspace_root), files)
    files.sort()
    return files


def _scan_dir(root: Path, dir_path: Path, gitignore: GitignoreFilter, files: list[str]) -> None:
    """Recursion helper for `_scan_workspace_files`: appends `dir_path`'s own non-ignored files
    (POSIX-relative to `root`) onto `files` and recurses into its non-ignored subdirectories,
    stopping early once `MAX_INDEXED_FILES` is reached."""
    try:
        entries = sorted(dir_path.iterdir(), key=lambda entry: entry.name)
    except OSError:
        return
    for entry in entries:
        if len(files) >= MAX_INDEXED_FILES:
            return
        if entry.is_symlink():
            # A symlinked directory is skipped outright (no cycle risk), mirroring
            # klorb.tools.util.dir_walk's own followlinks=False convention; a symlinked file is
            # indexed like any other.
            if not entry.is_dir() and not gitignore.is_ignored(entry, is_dir=False):
                files.append(entry.relative_to(root).as_posix())
            continue
        if entry.is_dir():
            if entry.name == GIT_DIR_NAME or gitignore.is_ignored(entry, is_dir=True):
                continue
            _scan_dir(root, entry, gitignore.descend(entry), files)
        elif not gitignore.is_ignored(entry, is_dir=False):
            files.append(entry.relative_to(root).as_posix())


class _ChangeHandler(FileSystemEventHandler):
    """Routes watchdog filesystem events into `WorkspaceFileIndex`'s debounced update pipeline.

    A plain file's own creation or deletion is applied as a single incremental add/remove; a
    directory event or any change to a `.gitignore` file forces a full rescan instead, since
    either can affect more paths than the one the event names -- a removed directory deletes
    every file beneath it in one event, and a `.gitignore` edit changes which paths the filter
    itself excludes. Ordinary file content edits are not watched at all: they can't change
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
    caller driving a UI must marshal back onto its own thread (e.g. Textual's
    `App.call_from_thread`) before touching widgets from it.
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

    @property
    def files(self) -> list[str]:
        """A thread-safe snapshot of the current, sorted file list."""
        with self._lock:
            return list(self._files)

    def start(self) -> None:
        """Run the initial scan synchronously, then start watching `workspace_root` in the
        background. A no-op if already started."""
        if self._observer is not None:
            return
        self._rescan()
        observer = Observer()
        observer.schedule(_ChangeHandler(self), str(self._workspace_root), recursive=True)
        observer.start()
        self._observer = observer
        logger.debug(
            "WorkspaceFileIndex watching %s for file/.gitignore create/delete events",
            self._workspace_root)

    def stop(self) -> None:
        """Cancel any pending debounced update and stop the background observer thread. Safe to
        call more than once, or when never started."""
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
        files = _scan_workspace_files(self._workspace_root)
        with self._lock:
            self._files = files
        logger.debug(
            "WorkspaceFileIndex indexed %d file(s) under %s", len(files), self._workspace_root)

    def _queue_created(self, abs_path: Path) -> None:
        """Record `abs_path` (an absolute, non-directory path a watchdog event just reported as
        created) for the next debounced flush, unless a `.gitignore` covering its own directory
        excludes it -- checked fresh via `GitignoreFilter.for_root` rather than a cached
        whole-tree filter, since only `abs_path`'s own ancestor chain of `.gitignore` files
        needs reading here, not a full rescan."""
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
        self._schedule_flush()

    def _queue_deleted(self, abs_path: Path) -> None:
        try:
            rel_path = abs_path.relative_to(self._workspace_root).as_posix()
        except ValueError:
            return
        with self._lock:
            self._pending_deleted.add(rel_path)
            self._pending_created.discard(rel_path)
        self._schedule_flush()

    def _queue_rescan(self) -> None:
        with self._lock:
            self._needs_rescan = True
        self._schedule_flush()

    def _schedule_flush(self) -> None:
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            timer = threading.Timer(_DEBOUNCE_SECONDS, self._flush)
            timer.daemon = True
            self._debounce_timer = timer
            timer.start()

    def _flush(self) -> None:
        with self._lock:
            rescan = self._needs_rescan
            created = self._pending_created
            deleted = self._pending_deleted
            self._needs_rescan = False
            self._pending_created = set()
            self._pending_deleted = set()
            self._debounce_timer = None
        if rescan:
            self._rescan()
        elif created or deleted:
            with self._lock:
                self._files = sorted((set(self._files) - deleted) | created)
        else:
            return
        self._on_changed()
