# © Copyright 2026 Aaron Kimball
"""Configures klorb's stdlib logging: a TextualHandler plus a per-session log file."""

import logging
from pathlib import Path

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
