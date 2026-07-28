# © Copyright 2026 Aaron Kimball
"""WebFetch-specific spill-file naming, built on the shared session-scoped tmpdir mechanism in
`klorb.tools.util.spill.SpillDir` — see that module for the directory lifecycle and
permission-grant half of the mechanism this reuses. When a `WebFetch` response exceeds
`tools.webFetch.spillBytes`, the body is written to a file inside this session-scoped tmpdir
rather than returned inline; the model can then `ReadFile`/`Grep` the spilled file via the
normal file tools.
"""

import re
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from klorb.tools.util.spill import SpillDir

if TYPE_CHECKING:
    from klorb.session import Session

_SPILL_DIR = SpillDir("WebFetch")


def _domain_to_snake_case(domain: str) -> str:
    """Convert a domain to a snake-case filename component.

    `www.example.com` becomes `www_example_com`.
    """
    return re.sub(r"[^a-zA-Z0-9]", "_", domain).strip("_").lower()


def get_or_create_tmpdir(session: "Session") -> Path:
    """Return the session-scoped tmpdir for WebFetch spill files, creating it on first use."""
    return _SPILL_DIR.get_or_create(session)


def grant_tmpdir_read_access(session: "Session", tmpdir_path: Path) -> None:
    """Auto-grant read access to `tmpdir_path` so a follow-up `ReadFile`/`Grep` call against a
    spilled file doesn't itself hit an `ask`."""
    _SPILL_DIR.grant_read_access(session, tmpdir_path)


def spill_file_path(tmpdir_path: Path, domain: str) -> Path:
    """Generate a unique spill file path inside `tmpdir_path` for the given domain."""
    snake_domain = _domain_to_snake_case(domain)
    random_hex = secrets.token_hex(4)
    return tmpdir_path / f"webfetch-{snake_domain}-{random_hex}.txt"
