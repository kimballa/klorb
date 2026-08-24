# © Copyright 2026 Aaron Kimball
"""`FileSystemWatcher`: the runtime counterpart to `klorb.hooks.config.
FileSystemModifiedEventConfig`.
"""

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from klorb.hooks.config import MIN_EVENT_DEBOUNCE_SECONDS, FileSystemModifiedEventConfig
from klorb.hooks.hook_api import EventInput, FileSystemUpdate
from klorb.tools.util.dir_walk import GIT_DIR_NAME
from klorb.tools.util.gitignore import GitignoreFilter

logger = logging.getLogger(__name__)

_FsUpdateKind = Literal["created", "deleted", "modified"]


def _path_matches(changed_path: Path, watch_target: Path) -> bool:
    """Whether `changed_path` falls under `watch_target`: equal to it (a file watch) or a
    descendant of it (a directory watch)."""
    return changed_path == watch_target or changed_path.is_relative_to(watch_target)


def _dedupe(updates: list[FileSystemUpdate]) -> list[FileSystemUpdate]:
    """Drop a repeat `(event, path)` pair, preserving first-seen order."""
    seen: set[tuple[str, str]] = set()
    deduped: list[FileSystemUpdate] = []
    for update in updates:
        key = (update.event, update.path)
        if key not in seen:
            seen.add(key)
            deduped.append(update)
    return deduped


class _FsChangeHandler(FileSystemEventHandler):
    """Routes watchdog filesystem events into `FileSystemWatcher`'s debounced batch. Directory
    events are dropped."""

    def __init__(self, watcher: "FileSystemWatcher") -> None:
        self._watcher = watcher

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._watcher._record("created", str(event.src_path))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._watcher._record("deleted", str(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._watcher._record("modified", str(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._watcher._record("deleted", str(event.src_path))
        self._watcher._record("created", str(event.dest_path))


class FileSystemWatcher:
    """Watches `entries`' configured paths under `workspace_root`. After each debounced burst
    of changes settles, `dispatch` is called once with whichever `entries` had at least one
    change fall under their own `watch` path, together with an `EventInput` batch of every
    matched change.
    """

    def __init__(
        self, workspace_root: Path, entries: list[FileSystemModifiedEventConfig], *,
        dispatch: Callable[[list[FileSystemModifiedEventConfig], EventInput], None],
        debounce_seconds: float = MIN_EVENT_DEBOUNCE_SECONDS,
    ) -> None:
        self._workspace_root = workspace_root
        self._entries = entries
        self._dispatch = dispatch
        self._debounce_seconds = debounce_seconds
        self._observer: BaseObserver | None = None
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._pending: list[FileSystemUpdate] = []
        self._closing_event = threading.Event()

    def start(self) -> None:
        """Start watching every `entries` path in the background. A no-op if already started,
        already closed, or `entries` is empty (nothing configured to watch)."""
        if self._observer is not None or self._closing_event.is_set() or not self._entries:
            return
        handler = _FsChangeHandler(self)
        watch_dirs = self._distinct_watch_dirs()
        observer = Observer()
        for watch_dir in watch_dirs:
            observer.schedule(handler, str(watch_dir), recursive=True)
        observer.start()
        self._observer = observer
        logger.debug(
            "FileSystemWatcher watching %d path(s) under %s for %d configured entr(y/ies)",
            len(watch_dirs), self._workspace_root, len(self._entries))

    def close(self) -> None:
        """Stop watching and cancel any pending debounce timer. Safe to call more than once, or
        when never started."""
        self._closing_event.set()
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None
            logger.debug("FileSystemWatcher stopped watching %s", self._workspace_root)

    def _distinct_watch_dirs(self) -> set[Path]:
        """The directories to actually register with the `Observer`: each entry's own `watch`
        target if it's a directory, else its parent (inotify has no way to watch a single file
        directly). Entries whose `watch` escapes the workspace root are skipped with a
        warning."""
        dirs: set[Path] = set()
        for entry in self._entries:
            target = (self._workspace_root / entry.watch).resolve()
            if not target.is_relative_to(self._workspace_root):
                logger.warning(
                    "Skipping FileSystemModified watch %r: resolves to %s, outside workspace %s",
                    entry.watch, target, self._workspace_root)
                continue
            dirs.add(target if target.is_dir() else target.parent)
        return dirs

    def _is_git_internal(self, abs_path: str) -> bool:
        """Whether `abs_path` falls inside a `.git` directory under the workspace root -- always
        excluded regardless of `apply_gitignore`, matching `klorb.tools.util.dir_walk`'s own
        unconditional `.git` exclusion."""
        path = Path(abs_path)
        try:
            relative = path.relative_to(self._workspace_root)
        except ValueError:
            relative = path
        return GIT_DIR_NAME in relative.parts

    def _record(self, event_type: _FsUpdateKind, abs_path: str) -> None:
        if self._is_git_internal(abs_path):
            return
        with self._lock:
            self._pending.append(FileSystemUpdate(event=event_type, path=abs_path))
            if self._timer is not None:
                self._timer.cancel()
            timer = threading.Timer(self._debounce_seconds, self._flush)
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _flush(self) -> None:
        with self._lock:
            raw_pending = self._pending
            self._pending = []
            self._timer = None
        if not raw_pending or self._closing_event.is_set():
            return
        pending = _dedupe(raw_pending)
        matched_entries: list[FileSystemModifiedEventConfig] = []
        matched_paths: set[str] = set()
        gitignore: GitignoreFilter | None = None
        for entry in self._entries:
            watch_target = (self._workspace_root / entry.watch).resolve()
            entry_updates = [
                update for update in pending if _path_matches(Path(update.path), watch_target)]
            if entry.apply_gitignore and entry_updates:
                if gitignore is None:
                    gitignore = GitignoreFilter.for_root(self._workspace_root, self._workspace_root)
                entry_updates = [
                    update for update in entry_updates
                    if not gitignore.is_ignored(Path(update.path), is_dir=False)]
            if entry_updates:
                matched_entries.append(entry)
                matched_paths.update(update.path for update in entry_updates)
        if not matched_entries:
            return
        matched_updates = [update for update in pending if update.path in matched_paths]
        try:
            self._dispatch(matched_entries, EventInput(
                hook="FileSystemModified", workspace_root=str(self._workspace_root),
                fs_updates=matched_updates))
        except Exception:
            # A new fs change independently re-arms the next debounce timer via `_record`, so
            # this batch's own dispatch failing doesn't stop future changes from being watched.
            logger.error("FileSystemModified dispatch failed for %d entr(y/ies).",
                         len(matched_entries), exc_info=True)
