# © Copyright 2026 Aaron Kimball
"""File access control: Concrete `PermissionsTable` resource kind governing read/write access to
individual files by exact path match, distinct from `klorb.permissions.directory_access`'s
containment-based directory rules. See docs/specs/permissions.md.

`klorb.permissions.workspace.resolve_and_evaluate_read()`/`resolve_and_evaluate_write()` consult
a `FileAccessTable` before any directory-level concern (the workspace-root boundary,
`readDirs`/`writeDirs`) is even considered: an exact match here is authoritative and its verdict
is used as-is, with no directory-level fallback. This lets a single file be carved out with its
own verdict even when the directory it lives in — or, for a path outside the workspace root
entirely, the hard workspace-root boundary itself — would otherwise deny, ask about, or exclude
every other file. See `klorb.resources.default-config.json`'s `readFiles`/`writeFiles.allow`
entries for the special character devices (`/dev/null`, `/dev/zero`, `/dev/random`,
`/dev/urandom`) this exists to support: those paths sit outside any workspace root, so no
directory-level rule could ever reach them.

This module deliberately has no dependency on `klorb.tools` or `klorb.session`, for the same
reason `klorb.permissions.directory_access` doesn't: `SessionConfig` (in `klorb.session`) holds
a `FileRules` field, so this module must not import `klorb.session` — doing so would create a
cycle.
"""

from pathlib import Path

from pydantic import BaseModel, Field

from klorb.permissions.directory_access import canonicalize_dir
from klorb.permissions.table import PermissionsTable


class FileRules(BaseModel):
    """One file-access direction's (`readFiles` or `writeFiles`) `deny`/`ask`/`allow` rule
    lists, as plain path data — mirrors `klorb.permissions.directory_access.DirRules`, except
    each entry names one specific file to match exactly rather than a directory to match by
    containment. See `FileAccessTable` for the evaluation logic that consumes this. Treated as
    immutable after construction, exactly like `DirRules`.
    """

    deny: list[Path] = Field(default_factory=list)
    ask: list[Path] = Field(default_factory=list)
    allow: list[Path] = Field(default_factory=list)


class FileAccessTable(PermissionsTable[Path]):
    """A `PermissionsTable` over canonicalized individual file paths: a rule matches a candidate
    only when it names the exact same file — no ancestor-or-equal containment, unlike
    `klorb.permissions.directory_access.DirectoryAccessTable`. Rule paths are canonicalized at
    construction time via `canonicalize_dir()`, the same primitive `DirectoryAccessTable` uses,
    so a rule like `"~/scratch.txt"` or a still-relative path behaves identically here (`~`
    expansion, workspace-relative join, symlink/`..` resolution).
    """

    def __init__(self, rules: FileRules, workspace_root: Path) -> None:
        super().__init__(
            deny=[canonicalize_dir(path, workspace_root) for path in rules.deny],
            ask=[canonicalize_dir(path, workspace_root) for path in rules.ask],
            allow=[canonicalize_dir(path, workspace_root) for path in rules.allow],
        )

    def _matches(self, rule: Path, candidate: Path) -> bool:
        return candidate == rule
