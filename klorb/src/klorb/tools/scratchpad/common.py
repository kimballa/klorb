# © Copyright 2026 Aaron Kimball
"""Owns the active session's scratchpad file: where it lives, how it's provisioned, and how a
`ReadScratchpad`/`EditScratchpad`/`SearchScratchpad` tool resolves it from a `ToolSetupContext`.
See docs/specs/scratchpad.md.

`Scratchpad` has no runtime dependency on `klorb.tools.setup_context` (only `scratchpad_path()`
below does, and only under `TYPE_CHECKING`) so that `klorb.session.Session` can import
`Scratchpad` directly without creating an import cycle — `klorb.tools.setup_context` imports
`klorb.session` for real, so a real (non-`TYPE_CHECKING`) import of it here, reachable from
`klorb.session`, would cycle back on itself.
"""

import atexit
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from klorb.tools.setup_context import ToolSetupContext

SCRATCHPAD_FILENAME = "SCRATCHPAD.md"
"""Filename `Scratchpad` gives a freshly created scratchpad file, within a fresh
`tempfile.mkdtemp()` directory, whenever it isn't handed an existing path to reuse."""


class Scratchpad:
    """Manages creation, tracking, and cleanup of one session's scratchpad file.

    Constructed with the same `scratchpad_path: str | None` a `Session` is constructed with
    (see `klorb.session.Session.__init__`): given a path, it's reused as-is (as a `Path`), on
    the assumption the file already exists — the multi-session/shared-scratchpad case, e.g. an
    operator and its subagents coordinating through one shared file, once agent-team dispatch
    exists (see docs/specs/scratchpad.md). Given `None`, a fresh `SCRATCHPAD_FILENAME` file is
    created inside a brand new `tempfile.mkdtemp()` directory, touched into existence
    immediately so `EditScratchpad`'s first call has a real, zero-length file to edit rather
    than a `FileNotFoundError`.

    `Session.__init__` registers `cleanup` as a teardown (see `Session.register_teardown`), so
    it runs once the session closes.
    """

    def __init__(self, scratchpad_path: str | None) -> None:
        self._owned_dir: Path | None = None
        """The directory this `Scratchpad` created and therefore owns, or `None` when
        `scratchpad_path` named an existing file to reuse instead — the sole switch `cleanup()`
        checks, so a caller-supplied scratchpad is never touched by it."""
        self._path = self._resolve(scratchpad_path)

    def _resolve(self, scratchpad_path: str | None) -> Path:
        if scratchpad_path is not None:
            return Path(scratchpad_path)
        scratchpad_dir = Path(tempfile.mkdtemp(prefix="klorb-scratchpad-"))
        # Registered immediately after creation, before anything else can raise, so this
        # directory is always swept on process exit even when `cleanup()` never runs -- the
        # final active session is never `close()`d on normal TUI exit, and a crash or SIGKILL
        # skips teardown entirely. Mirrors `BashTool`'s spilled-output tmpdir handling.
        atexit.register(shutil.rmtree, scratchpad_dir, ignore_errors=True)
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
        reuse — that file's lifecycle belongs to whatever created it, not to this `Scratchpad`.
        Safe to call more than once (`shutil.rmtree(..., ignore_errors=True)`), matching every
        other teardown callback's idempotency contract (see `Session.register_teardown`).

        This is the eager path, run by `Session.close()` so a switched-away session's directory
        goes right away; an `atexit` hook registered at creation time (see `_resolve`) is the
        backstop that sweeps the directory on process exit when `close()` never runs at all.
        """
        if self._owned_dir is not None:
            shutil.rmtree(self._owned_dir, ignore_errors=True)


def scratchpad_path(context: "ToolSetupContext") -> Path:
    """Return the active session's scratchpad file path, for a `ReadScratchpad`/
    `EditScratchpad`/`SearchScratchpad` tool's `apply()` to read/write directly.

    Raises `ValueError` if `context` wasn't built with a real `Session` (e.g. a `ToolSetupContext`
    constructed directly, as most unit tests for other tools do) -- the scratchpad is per-session
    state, so a Scratchpad tool has nothing to read or write without one.
    """
    if context.session is None:
        raise ValueError("Scratchpad tools require an active session")
    return context.session.scratchpad.path
