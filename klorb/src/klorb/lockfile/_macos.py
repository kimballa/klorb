# © Copyright 2026 Aaron Kimball
"""`PosixLockfile`: the macOS `klorb.lockfile.Lockfile` implementation using a non-blocking
classic advisory record lock."""

import logging
import os
import struct
from pathlib import Path

logger = logging.getLogger(__name__)

F_RDLCK = 0
F_WRLCK = 1
F_UNLCK = 2
"""`l_type` values for a `struct flock`."""

_FLOCK_STRUCT_FORMAT = "hhqqi"
"""`struct flock` on 64-bit macOS: `short l_type, short l_whence, off_t l_start, off_t l_len,
pid_t l_pid`."""


class PosixLockfile:
    """macOS `Lockfile`: a non-blocking, whole-file exclusive classic `fcntl` record lock via
    `fcntl.fcntl(fd, fcntl.F_SETLK, ...)`."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    @property
    def path(self) -> Path:
        return self._path

    def try_acquire(self) -> bool:
        if self._fd is not None:
            return True
        import fcntl

        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o666)
        lock_data = struct.pack(_FLOCK_STRUCT_FORMAT, F_WRLCK, 0, 0, 0, 0)
        try:
            fcntl.fcntl(fd, fcntl.F_SETLK, lock_data)
        except OSError:
            os.close(fd)
            logger.debug("Lock %s is already held by another process.", self._path)
            return False
        self._fd = fd
        logger.debug("Acquired POSIX record lock on %s (fd=%d).", self._path, fd)
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        os.close(self._fd)
        logger.debug("Released POSIX record lock on %s.", self._path)
        self._fd = None

    def is_held_by_other(self) -> bool:
        if self._fd is not None:
            return False
        if not self._path.is_file():
            return False
        import fcntl

        fd = os.open(self._path, os.O_RDWR)
        lock_data = struct.pack(_FLOCK_STRUCT_FORMAT, F_WRLCK, 0, 0, 0, 0)
        try:
            result = fcntl.fcntl(fd, fcntl.F_GETLK, lock_data)
        finally:
            os.close(fd)
        l_type, *_rest = struct.unpack(_FLOCK_STRUCT_FORMAT, result)
        return bool(l_type != F_UNLCK)
