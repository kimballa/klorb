# © Copyright 2026 Aaron Kimball
"""`OfdLockfile`: the Linux/WSL `klorb.lockfile.Lockfile` implementation, using a non-blocking
Open File Description lock (`F_OFD_SETLK`) rather than a classic PID-associated `fcntl` lock --
an OFD lock is tied to the open file description (this process's file descriptor), not the
process itself, so it behaves correctly even if this process later forks or the descriptor is
duplicated. `F_OFD_SETLK`/`F_OFD_GETLK` (37/36) aren't exposed as named constants by Python's
`fcntl` module (a Linux-specific extension not present in every libc's `<fcntl.h>` binding), so
they're defined here as raw command numbers, matching the standard glibc/Linux kernel UAPI
values. Imported lazily by `klorb.lockfile.create_lockfile` only on `sys.platform == "linux"`
(which also covers WSL) -- never imported on a platform where these commands don't exist.
"""

import logging
import os
import struct
from pathlib import Path

logger = logging.getLogger(__name__)

F_OFD_GETLK = 36
F_OFD_SETLK = 37
F_RDLCK = 0
F_WRLCK = 1
F_UNLCK = 2
"""`l_type` values for a `struct flock` -- not exposed by Python's `fcntl` module (unlike
`F_GETLK`/`F_SETLK` themselves), so defined here as the standard Linux/glibc `<fcntl.h>`
values."""

_FLOCK_STRUCT_FORMAT = "hhqqi"
"""`struct flock` on 64-bit Linux: `short l_type, short l_whence, off_t l_start, off_t l_len,
pid_t l_pid` -- `off_t` is 8 bytes on every 64-bit Linux target klorb runs on."""


class OfdLockfile:
    """Linux/WSL `Lockfile`: a non-blocking, whole-file exclusive OFD lock via
    `fcntl.fcntl(fd, F_OFD_SETLK, ...)`. See `klorb.lockfile.Lockfile` for the interface
    contract."""

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
            fcntl.fcntl(fd, F_OFD_SETLK, lock_data)
        except OSError:
            os.close(fd)
            logger.debug("Lock %s is already held by another process.", self._path)
            return False
        self._fd = fd
        logger.debug("Acquired OFD lock on %s (fd=%d).", self._path, fd)
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        os.close(self._fd)
        logger.debug("Released OFD lock on %s.", self._path)
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
            result = fcntl.fcntl(fd, F_OFD_GETLK, lock_data)
        finally:
            os.close(fd)
        l_type, *_rest = struct.unpack(_FLOCK_STRUCT_FORMAT, result)
        return bool(l_type != F_UNLCK)
