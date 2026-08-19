# © Copyright 2026 Aaron Kimball
"""Session-scoped temp-directory management for tools that spill oversized results to disk."""

import atexit
import logging
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from klorb.permissions.directory_access import DirRules

if TYPE_CHECKING:
    from klorb.session import Session

logger = logging.getLogger(__name__)

_TMPDIR_KEY = "tmpdir"
_DIR_ADDED_KEY = "tmpdir_dir_added"


class SpillDir:
    """One tool's session-scoped spill directory. `tool_name` namespaces both the tmpdir's
    `mkdtemp()` prefix (`klorb-<tool_name lowercased>-`) and the tool's own slot in
    `session.tool_state`, so distinct tools spilling within the same session get distinct
    directories that never collide. Holds no state of its own beyond `tool_name`, so a `Tool`
    can hold a single `SpillDir` instance for its whole lifetime and share it across every
    `apply()` call.
    """

    def __init__(self, tool_name: str) -> None:
        self._tool_name = tool_name
        self._tmp_dir_prefix = f"klorb-{tool_name.lower()}-"

    def get_or_create(self, session: "Session") -> Path:
        """Return this tool's spill tmpdir for `session`, creating it (and registering its
        cleanup) on first use, and reusing the same directory for every later call within the
        same session.
        """
        tool_state: dict = session.tool_state.setdefault(self._tool_name, {})  # type: ignore[assignment]
        existing = tool_state.get(_TMPDIR_KEY)
        if existing is not None:
            return Path(existing)

        tmpdir_path = Path(tempfile.mkdtemp(prefix=self._tmp_dir_prefix))
        logger.debug("Created %s spill tmpdir: %s", self._tool_name, tmpdir_path)
        tool_state[_TMPDIR_KEY] = str(tmpdir_path)

        def cleanup_once() -> None:
            # Whichever path runs first -- `session.close()`'s eager teardown, or the interpreter
            # actually reaching this atexit callback -- unregisters the atexit registration so the
            # other path (if it still runs later) doesn't call `_cleanup()`, and so log, a second
            # time.
            atexit.unregister(cleanup_once)
            self._cleanup(tmpdir_path)

        session.register_teardown(self._tool_name, cleanup_once)
        atexit.register(cleanup_once)

        return tmpdir_path

    def _cleanup(self, tmpdir_path: Path) -> None:
        """Remove the spill tmpdir, ignoring errors."""
        shutil.rmtree(tmpdir_path, ignore_errors=True)
        logger.debug("Cleaned up %s spill tmpdir: %s", self._tool_name, tmpdir_path)

    def grant_read_access(self, session: "Session", tmpdir_path: Path) -> None:
        """Auto-grant read access to `tmpdir_path` (once per session) so a follow-up
        `ReadFile`/`Grep` call against a spilled file doesn't itself hit an `ask`.

        Appends to `session.config.read_dirs.allow` (reassigning the whole `DirRules`, never
        mutating its list in place, per `DirRules`'s documented contract).
        """
        tool_state: dict = session.tool_state.setdefault(self._tool_name, {})  # type: ignore[assignment]
        if tool_state.get(_DIR_ADDED_KEY):
            return

        read_dirs = session.config.read_dirs
        session.config.read_dirs = DirRules(
            deny=list(read_dirs.deny), ask=list(read_dirs.ask),
            allow=[*read_dirs.allow, tmpdir_path])
        tool_state[_DIR_ADDED_KEY] = True
        logger.debug("Granted read access to %s spill tmpdir: %s", self._tool_name, tmpdir_path)
