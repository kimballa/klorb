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

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from klorb.tools.setup_context import ToolSetupContext

SCRATCHPAD_FILENAME = "SCRATCHPAD.md"
"""Filename `Scratchpad` gives a freshly created scratchpad file, within a fresh
`tempfile.mkdtemp()` directory, whenever it isn't handed an existing path to reuse."""


class Scratchpad:
    """Manages creation and tracking of one session's scratchpad file.

    Constructed with the same `scratchpad_path: str | None` a `Session` is constructed with
    (see `klorb.session.Session.__init__`): given a path, it's reused as-is (as a `Path`), on
    the assumption the file already exists — the multi-session/shared-scratchpad case, e.g. a
    coordinator and its subagents coordinating through one shared file, once agent-team dispatch
    exists (see docs/specs/scratchpad.md). Given `None`, a fresh `SCRATCHPAD_FILENAME` file is
    created inside a brand new `tempfile.mkdtemp()` directory, touched into existence
    immediately so `EditScratchpad`'s first call has a real, zero-length file to edit rather
    than a `FileNotFoundError`.
    """

    def __init__(self, scratchpad_path: str | None) -> None:
        self._path = self._resolve(scratchpad_path)

    def _resolve(self, scratchpad_path: str | None) -> Path:
        if scratchpad_path is not None:
            return Path(scratchpad_path)
        scratchpad_dir = Path(tempfile.mkdtemp(prefix="klorb-scratchpad-"))
        path = scratchpad_dir / SCRATCHPAD_FILENAME
        path.touch()
        return path

    @property
    def path(self) -> Path:
        """Return this scratchpad's file path."""
        return self._path


def scratchpad_path(context: "ToolSetupContext") -> Path:
    """Return the active session's scratchpad file path, for a `ReadScratchpad`/
    `EditScratchpad`/`SearchScratchpad` tool's `apply()` to read/write directly.

    Raises `ValueError` if `context` wasn't built with a real `Session` (e.g. a `ToolSetupContext`
    constructed directly, as most unit tests for other tools do) -- the scratchpad is per-session
    state, so a Scratchpad tool has nothing to read or write without one.
    """
    if context.session is None:
        raise ValueError("Scratchpad tools require an active session")
    return context.session.scratchpad_path
