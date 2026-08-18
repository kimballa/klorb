# © Copyright 2026 Aaron Kimball
"""`klorb.lockfile`: a non-blocking, whole-file exclusive lock on a path, used to tell whether
some other klorb process currently owns a shared resource before writing to or restoring it.
"""

import logging
import sys
import time
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_INITIAL_DELAY_SECONDS = 0.1
"""Defaults for `acquire_lockfile_with_backoff`: 4 attempts total, starting at a 100ms delay and
doubling each retry."""


class Lockfile(Protocol):
    """A non-blocking, whole-file exclusive (write) lock on `path`, held for as long as the
    acquiring process keeps it open. The OS releases the underlying lock automatically if the
    process dies, but a long-lived holder must still call `release()` explicitly on a clean
    shutdown."""

    @property
    def path(self) -> Path:
        """The lock file's path on disk."""
        ...

    def try_acquire(self) -> bool:
        """Attempt to acquire the lock without blocking. Returns `True` on success,
        `False` if another process currently holds it."""
        ...

    def release(self) -> None:
        """Release the lock if this instance holds it; a no-op otherwise. Does not delete
        `path` itself."""
        ...

    def is_held_by_other(self) -> bool:
        """Probe whether some *other* process currently holds this lock, without attempting to
        acquire it for this process. Used to check "is this session occupied?" without
        perturbing a lock this process doesn't already hold."""
        ...


def create_lockfile(path: Path) -> Lockfile:
    """Return the `Lockfile` implementation appropriate for the running platform: an
    Open-File-Description lock (`klorb.lockfile._linux.OfdLockfile`) on Linux/WSL
    (`sys.platform == "linux"`), or a classic POSIX record lock
    (`klorb.lockfile._macos.PosixLockfile`) on macOS (`sys.platform == "darwin"`). Raises
    `NotImplementedError` on any other platform."""
    if sys.platform == "linux":
        from klorb.lockfile._linux import OfdLockfile
        return OfdLockfile(path)
    # mypy's configured platform is always "linux" in this repo's CI, so it statically proves
    # this branch unreachable there; it's real code, exercised at runtime on an actual macOS
    # machine, just never type-checked on one.
    if sys.platform == "darwin":  # type: ignore[unreachable]
        from klorb.lockfile._macos import PosixLockfile
        return PosixLockfile(path)
    raise NotImplementedError(f"klorb.lockfile has no Lockfile implementation for {sys.platform!r}")


def acquire_lockfile_with_backoff(
    path: Path, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_delay_seconds: float = DEFAULT_INITIAL_DELAY_SECONDS,
) -> Lockfile | None:
    """Create a `Lockfile` at `path` and try to acquire it, retrying on contention up to
    `max_attempts` times total with exponential backoff starting at `initial_delay_seconds`
    between attempts. Returns the acquired `Lockfile`, or `None` if every attempt lost the
    race.

    Intended for short-lived, contended-but-quick locks held only for the duration of one
    read-modify-write of a shared index file."""
    lock = create_lockfile(path)
    for attempt in range(max_attempts):
        if lock.try_acquire():
            if attempt > 0:
                logger.debug("Acquired lock %s on attempt %d/%d.", path, attempt + 1, max_attempts)
            return lock
        if attempt + 1 >= max_attempts:
            break
        delay = initial_delay_seconds * (2 ** attempt)
        logger.debug(
            "Lock %s contended (attempt %d/%d); retrying in %.2fs.",
            path, attempt + 1, max_attempts, delay)
        time.sleep(delay)
    logger.warning("Failed to acquire lock %s after %d attempt(s).", path, max_attempts)
    return None


__all__ = [
    "Lockfile",
    "create_lockfile",
    "acquire_lockfile_with_backoff",
]
