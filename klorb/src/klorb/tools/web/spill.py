# © Copyright 2026 Aaron Kimball
"""Session-scoped temporary directory management for `WebFetch` spill files.

When a `WebFetch` response exceeds `tools.webFetch.spillBytes`, the body is written to a
file inside a session-scoped `tempfile.TemporaryDirectory` rather than returned inline.
The model can then `ReadFile`/`Grep` the spilled file via the normal file tools — the
same pattern `BashTool` uses for its own spilled `stdout`/`stderr` directories.
"""

import atexit
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from klorb.permissions.directory_access import DirRules

if TYPE_CHECKING:
    from klorb.session import Session

logger = logging.getLogger(__name__)

_TOOL_STATE_KEY = "WebFetch"
_TMPDIR_KEY = "tmpdir"
_DIR_ADDED_KEY = "tmpdir_dir_added"
_TEARDOWN_SUBJECT = "WebFetch"

_TMP_DIR_PREFIX = "klorb-webfetch-"


def _domain_to_snake_case(domain: str) -> str:
    """Convert a domain to a snake-case filename component.

    `www.example.com` becomes `www_example_com`.
    """
    return re.sub(r"[^a-zA-Z0-9]", "_", domain).strip("_").lower()


def get_or_create_tmpdir(session: "Session") -> Path:
    """Return the session-scoped tmpdir for WebFetch spill files, creating it on first use.

    The tmpdir is created once per session and cleaned up via `Session.register_teardown`
    (with an `atexit` fallback). On first use, its path is injected into
    `session_config.read_dirs.allow` so the model can `ReadFile`/`Grep` spilled files.
    """
    tool_state: dict = session.tool_state.setdefault(_TOOL_STATE_KEY, {})  # type: ignore[assignment]
    tmpdir_path = tool_state.get(_TMPDIR_KEY)
    if tmpdir_path is not None:
        return Path(tmpdir_path)

    tmpdir = tempfile.mkdtemp(prefix=_TMP_DIR_PREFIX)
    tmpdir_path = Path(tmpdir)
    logger.debug("Created WebFetch spill tmpdir: %s", tmpdir_path)
    tool_state[_TMPDIR_KEY] = str(tmpdir_path)

    session.register_teardown(_TEARDOWN_SUBJECT, lambda: _cleanup(tmpdir_path))
    atexit.register(_cleanup, tmpdir_path)

    return tmpdir_path


def _cleanup(tmpdir_path: Path) -> None:
    """Remove the spill tmpdir, ignoring errors (belt-and-suspenders teardown)."""
    shutil.rmtree(tmpdir_path, ignore_errors=True)
    logger.debug("Cleaned up WebFetch spill tmpdir: %s", tmpdir_path)


def grant_tmpdir_read_access(session: "Session", tmpdir_path: Path) -> None:
    """Auto-grant read access to `tmpdir_path` so a follow-up `ReadFile`/`Grep` call
    against a spilled file doesn't itself hit an `ask`.

    Appends to `session_config.read_dirs.allow` (reassigning the whole `DirRules`,
    never mutating its list in place, per `DirRules`'s documented contract).
    """
    tool_state: dict = session.tool_state.setdefault(_TOOL_STATE_KEY, {})  # type: ignore[assignment]
    if tool_state.get(_DIR_ADDED_KEY):
        return

    read_dirs = session.config.read_dirs
    session.config.read_dirs = DirRules(
        deny=list(read_dirs.deny),
        ask=list(read_dirs.ask),
        allow=[*read_dirs.allow, tmpdir_path],
    )
    tool_state[_DIR_ADDED_KEY] = True
    logger.debug("Granted read access to WebFetch spill tmpdir: %s", tmpdir_path)


def spill_file_path(tmpdir_path: Path, domain: str) -> Path:
    """Generate a unique spill file path inside `tmpdir_path` for the given domain."""
    import secrets
    snake_domain = _domain_to_snake_case(domain)
    random_hex = secrets.token_hex(4)
    return tmpdir_path / f"webfetch-{snake_domain}-{random_hex}.txt"
