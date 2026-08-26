# © Copyright 2026 Aaron Kimball
"""`FileActivityTracker`: the Files panel's record of every file read or written this process has
seen, across the root session and every subagent beneath it."""

import threading
from dataclasses import dataclass

from klorb.session import FileAccessMode


@dataclass(frozen=True)
class FileActivityEntry:
    """One file `FileActivityTracker` has recorded: its absolute path and whether it's ever been
    written to, `"write"` taking precedence over `"read"`."""

    abs_path: str
    mode: FileAccessMode


class FileActivityTracker:
    """Records every file a file I/O tool has touched, in first-access order, guarded by a lock
    since `record()` and `entries()` run on different threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._order: list[str] = []
        self._modes: dict[str, FileAccessMode] = {}

    def record(self, abs_path: str, mode: FileAccessMode) -> None:
        """Record one access to `abs_path`: a first-time path is recorded as `mode`; an existing
        entry only ever upgrades from `"read"` to `"write"`."""
        with self._lock:
            if abs_path not in self._modes:
                self._order.append(abs_path)
                self._modes[abs_path] = mode
            elif mode == "write":
                self._modes[abs_path] = "write"

    def entries(self) -> list[FileActivityEntry]:
        """Every recorded file, in first-access order."""
        with self._lock:
            return [FileActivityEntry(abs_path=path, mode=self._modes[path]) for path in self._order]
