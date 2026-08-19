# © Copyright 2026 Aaron Kimball
"""Owns the active session's scratchpad file: where it lives, how it's provisioned, and how a
`ReadScratchpad`/`EditScratchpad`/`SearchScratchpad` tool resolves it from a `ToolSetupContext`.
See docs/specs/scratchpad.md.
"""

import atexit
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from klorb.tools.setup_context import ToolSetupContext

SCRATCHPAD_FILENAME = "SCRATCHPAD.md"
"""Filename `Scratchpad` gives a freshly created scratchpad file, within a fresh
`tempfile.mkdtemp()` directory, whenever it isn't handed an existing path to reuse."""


class Scratchpad:
    """Manages creation, tracking, and cleanup of one session's scratchpad file.

    Constructed with the same `scratchpad_path: str | None` a `Session` is constructed with.
    Given a path, it's reused as-is (as a `Path`), on the assumption the file already exists.
    Given `None`, a fresh `SCRATCHPAD_FILENAME` file is created inside a brand new
    `tempfile.mkdtemp()` directory, touched into existence immediately so `EditScratchpad`'s
    first call has a real, zero-length file to edit rather than a `FileNotFoundError`.

    `Session.__init__` registers `cleanup` as a teardown so it runs once the session closes.
    """

    def __init__(self, scratchpad_path: str | None) -> None:
        self._owned_dir: Path | None = None
        """The directory this `Scratchpad` created and therefore owns, or `None` when
        `scratchpad_path` named an existing file to reuse instead."""
        self._cleanup_once: Callable[[], None] | None = None
        """The closure `_resolve()` builds for an owned `_owned_dir`."""
        self._path = self._resolve(scratchpad_path)

    def _resolve(self, scratchpad_path: str | None) -> Path:
        if scratchpad_path is not None:
            return Path(scratchpad_path)
        scratchpad_dir = Path(tempfile.mkdtemp(prefix="klorb-scratchpad-"))

        def cleanup_once() -> None:
            # Whichever path removes this directory first -- `cleanup()` (Session.close()'s
            # eager teardown), or the interpreter actually reaching this atexit callback --
            # unregisters the atexit registration so the other one, if it still runs later,
            # doesn't `rmtree` an already-gone directory again.
            atexit.unregister(cleanup_once)
            shutil.rmtree(scratchpad_dir, ignore_errors=True)

        # Registered immediately after creation, before anything else can raise, so this
        # directory is always swept on process exit even when `cleanup()` never runs.
        atexit.register(cleanup_once)
        self._cleanup_once = cleanup_once
        path = scratchpad_dir / SCRATCHPAD_FILENAME
        path.touch()
        self._owned_dir = scratchpad_dir
        return path

    @property
    def path(self) -> Path:
        """Return this scratchpad's file path."""
        return self._path

    def cleanup(self) -> None:
        """Remove the directory this `Scratchpad` created (if any), and everything in it.

        A no-op when this `Scratchpad` was constructed with an existing `scratchpad_path` to
        reuse. Safe to call more than once.

        This is the eager path, run by `Session.close()` so a switched-away session's directory
        goes right away; an `atexit` hook registered at creation time is the backstop that
        sweeps the directory on process exit when `close()` never runs.
        """
        if self._cleanup_once is not None:
            self._cleanup_once()


def scratchpad_path(context: "ToolSetupContext") -> Path:
    """Return the active session's scratchpad file path, for a `ReadScratchpad`/
    `EditScratchpad`/`SearchScratchpad` tool's `apply()` to read/write directly.

    Raises `ValueError` if `context` wasn't built with a real `Session`.
    """
    if context.session is None:
        raise ValueError("Scratchpad tools require an active session")
    return context.session.scratchpad.path
