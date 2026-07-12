# © Copyright 2026 Aaron Kimball
"""Configures klorb's stdlib logging: a TextualHandler plus a per-session log file."""

import io
import logging
import tempfile
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import TextIO

from textual.logging import TextualHandler

from klorb.paths import SESSION_LOGS_DIR

logger = logging.getLogger(__name__)

DEFAULT_MAX_SESSION_LOG_FILES = 12
"""Default hard cap on how many `*.log` files `prune_session_logs()` leaves in the session-logs
directory once a new session log is opened (counting the new one). See
docs/specs/paths-and-logging.md and
docs/adrs/prune-session-logs-to-newest-within-count-and-byte-caps.md."""

DEFAULT_MAX_SESSION_LOG_BYTES = 32 * 1024 * 1024
"""Default hard cap on the combined size of the `*.log` files `prune_session_logs()` retains.
The newly-opened session's own file is not yet counted against this (it starts empty and grows
over the session); this bounds the accumulated *older* logs. The single newest existing log is
always retained even when it alone exceeds this, so a big log is never deleted merely for being
big — see `prune_session_logs()`."""


def session_log_path(session_id: str) -> Path:
    """Build the session log file path for the given session id: SESSION_LOGS_DIR/<id>.log."""
    return SESSION_LOGS_DIR / f"{session_id}.log"


def prune_session_logs(
    logs_dir: Path,
    *,
    keep_path: Path,
    max_files: int = DEFAULT_MAX_SESSION_LOG_FILES,
    max_bytes: int = DEFAULT_MAX_SESSION_LOG_BYTES,
) -> None:
    """Delete the oldest `*.log` files in `logs_dir` so that, once the new log file `keep_path`
    is opened, the directory holds at most `max_files` files totaling at most `max_bytes` bytes,
    keeping the most-recently-modified files. Both caps are hard caps; the more restrictive one
    wins.

    `keep_path` is the log about to be opened (it need not exist yet); it is reserved a slot
    against `max_files` and never deleted. Recency is by file modification time, so a long
    session whose file is still being written stays "newest".

    Never deletes so many files that fewer than one log remains: the single newest existing file
    is always retained regardless of its size (the "not less than one log file of any length"
    floor), so a session log larger than `max_bytes` on its own is kept rather than erased. Older
    files are retained only while they still fit under `max_bytes`. A file that can't be removed
    (permissions, a race) is logged and skipped rather than raised.
    """
    logger.debug("Pruning .log files in %s; max cnt=%s, MB=%s",
                 logs_dir, max_files, max_bytes / 1000000)
    candidates = [p for p in logs_dir.glob("*.log") if p.is_file() and p != keep_path]
    if not candidates:
        return

    # Newest first. One slot is reserved for `keep_path`, so at most max_files-1 existing files
    # may be retained alongside it.
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    keep_count = max(0, max_files - 1)

    kept: set[Path] = set()
    total_bytes = 0
    for index, path in enumerate(candidates[:keep_count]):
        size = path.stat().st_size
        # index 0 is the newest existing log: keep it regardless of size (the floor). Older
        # logs are kept only while the running total stays within the byte cap; once one
        # doesn't fit, it and everything older than it are pruned.
        if index == 0 or total_bytes + size <= max_bytes:
            kept.add(path)
            total_bytes += size
        else:
            break

    for path in candidates:
        if path in kept:
            continue
        try:
            logger.info("Removing old log file: %s", path)
            path.unlink()
        except OSError:
            logger.warning("Could not remove old session log %s", path, exc_info=True)


def configure_logging(
    *,
    repl_mode: bool,
    log_path: Path | None,
    max_log_files: int = DEFAULT_MAX_SESSION_LOG_FILES,
    max_log_bytes: int = DEFAULT_MAX_SESSION_LOG_BYTES,
) -> None:
    """Configure the root logger.

    In the REPL (`repl_mode=True`), logs go to a `TextualHandler` (the active Textual
    app's console, or stderr when none is running). Otherwise, for a one-shot prompt,
    logs go directly to stderr. When `log_path` is given, log records are also written to
    that file, and the session-logs directory is first pruned (via `prune_session_logs()`,
    bounded by `max_log_files`/`max_log_bytes`) so old logs don't accumulate without limit.
    """
    handlers: list[logging.Handler] = [TextualHandler()] if repl_mode else [logging.StreamHandler()]

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        prune_session_logs(
            log_path.parent, keep_path=log_path, max_files=max_log_files, max_bytes=max_log_bytes)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))

    logging.basicConfig(level="NOTSET", handlers=handlers, force=True)


def crash_log_path(workspace_root: Path) -> Path:
    """Build the path a Textual crash dump for `workspace_root` would be written to:
    `<tempdir>/klorb-crash-<workspace basename>-<timestamp>.log`, timestamped to the second at
    call time so repeated crashes in the same workspace don't collide.
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    basename = workspace_root.name or "workspace"
    return Path(tempfile.gettempdir()) / f"klorb-crash-{basename}-{timestamp}.log"


class CrashLogTee(TextIO):
    """A `typing.TextIO`, suitable as a `rich.console.Console.file` with no cast needed, that
    duplicates every write to a real output stream (`stderr`) and a crash log file, so
    Textual's `App._print_error_renderables()` — which prints an unhandled exception's
    traceback to `App.error_console` on its way out — also lands the same text in a file a
    user can attach to a bug report.

    The log file is opened lazily on the first write rather than eagerly in `__init__`, so a
    session that never crashes never creates a file in `/tmp`: `error_console` is written to
    only when there's actually a crash to report (see `App._print_error_renderables`). If the
    file can't be opened (permissions, a full disk), writes silently fall back to `stream`
    alone rather than raising out of Rich's render path; if a write to `stream` itself fails,
    the file write already happened first, so the crash is still captured on disk. Call
    `opened_log_path` after the run to find out whether (and where) anything was actually
    written.

    This is a write-only stream: `write`/`flush`/`isatty`/`encoding`/`close` are the only
    members with real behavior — everything else `typing.IO[str]` requires (`read*`, `seek`,
    `tell`, `truncate`, `fileno`, iteration) is unsupported and raises `io.UnsupportedOperation`
    or reports `False`, matching how e.g. a pipe or `io.StringIO` opened write-only behaves for
    the same operations, since Rich's `Console` never calls them on `.file`.
    """

    def __init__(self, stream: TextIO, log_path: Path) -> None:
        self._stream = stream
        self._log_path = log_path
        self._log_file: TextIO | None = None
        self._log_open_failed = False
        self._closed = False

    def _ensure_log_file(self) -> TextIO | None:
        if self._log_file is None and not self._log_open_failed:
            try:
                self._log_file = self._log_path.open("w", encoding="utf-8")
            except OSError:
                logger.warning("Could not open crash log file %s", self._log_path, exc_info=True)
                self._log_open_failed = True
        return self._log_file

    def opened_log_path(self) -> Path | None:
        """The crash log path if a write has actually opened it, else `None`."""
        return self._log_path if self._log_file is not None else None

    def write(self, s: str) -> int:
        log_file = self._ensure_log_file()
        if log_file is not None:
            try:
                log_file.write(s)
            except OSError:
                logger.warning("Could not write to crash log file %s", self._log_path, exc_info=True)
        return self._stream.write(s)

    def flush(self) -> None:
        if self._log_file is not None:
            try:
                self._log_file.flush()
            except OSError:
                pass
        self._stream.flush()

    def isatty(self) -> bool:
        return self._stream.isatty()

    @property
    def encoding(self) -> str:
        return getattr(self._stream, "encoding", "utf-8") or "utf-8"

    def close(self) -> None:
        """Close the crash log file, if one was opened. Never closes `stream` (typically
        `sys.stderr`) — this object doesn't own it, so it's the caller's to close, if anyone's.
        """
        if self._log_file is not None:
            self._log_file.close()
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    def fileno(self) -> int:
        raise io.UnsupportedOperation("fileno")

    def readable(self) -> bool:
        return False

    def read(self, n: int = -1) -> str:
        raise io.UnsupportedOperation("read")

    def readline(self, limit: int = -1) -> str:
        raise io.UnsupportedOperation("readline")

    def readlines(self, hint: int = -1) -> list[str]:
        raise io.UnsupportedOperation("readlines")

    def seekable(self) -> bool:
        return False

    def seek(self, offset: int, whence: int = 0) -> int:
        raise io.UnsupportedOperation("seek")

    def tell(self) -> int:
        raise io.UnsupportedOperation("tell")

    def truncate(self, size: int | None = None) -> int:
        raise io.UnsupportedOperation("truncate")

    def writable(self) -> bool:
        return True

    def writelines(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.write(line)

    def __next__(self) -> str:
        raise io.UnsupportedOperation("__next__")

    def __iter__(self) -> Iterator[str]:
        return self

    def __enter__(self) -> "CrashLogTee":
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
