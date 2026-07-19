# © Copyright 2026 Aaron Kimball
"""Directory access control: Concrete `PermissionsTable` resource kind, governing
which directories klorb's file tools may read from and write to. See
docs/specs/permissions.md and docs/adrs/gate-read-hard-boundary-on-workspace-trust.md.

This module deliberately has no dependency on `klorb.tools` or `klorb.session`: `SessionConfig`
(in `klorb.session`) holds a `DirRules` field, so this module must not import `klorb.session` —
doing so would create a cycle. The policy functions that combine path resolution with the
tables defined here (`evaluate_write()`, `resolve_and_evaluate_read()`) live in
`klorb.permissions.workspace` instead, which needs a `ToolSetupContext` type (defined in
`klorb.tools.setup_context`, which itself depends on `klorb.session`) and so imports it under
`TYPE_CHECKING` only, avoiding the same cycle.
"""

import atexit
import logging
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from klorb.paths import KLORB_CONFIG_DIR, KLORB_DATA_DIR, KLORB_STATE_DIR
from klorb.permissions.table import PermissionsTable

if TYPE_CHECKING:
    # SessionConfig pulls in this module (for DirRules), so the import stays TYPE_CHECKING-only
    # to avoid a cycle -- create_tempdir_for_session() only ever touches session_config's
    # attributes, never instantiates a SessionConfig itself.
    from klorb.session import SessionConfig

logger = logging.getLogger(__name__)

KLORB_PROJECT_DIR_NAME = ".klorb"
"""Name of the directory (immediate child of a workspace root) that holds klorb's own
per-project config and state. Shared with `klorb.process_config.project_config_path()`,
which imports this constant rather than duplicating the literal."""


class DirRules(BaseModel):
    """One directory-access direction's (`readDirs` or `writeDirs`) `deny`/`ask`/`allow` rule
    lists, as plain path data — see `PermissionsTable` for the evaluation logic that consumes
    this. Treated as immutable after construction: nothing in this codebase mutates these lists
    in place post-construction (unlike `SessionConfig.max_tool_calls_per_turn`), so
    `SessionConfig.model_copy()`'s shallow copy sharing the underlying list objects across a
    template and its cloned sessions is harmless.
    """

    deny: list[Path] = Field(default_factory=list)
    ask: list[Path] = Field(default_factory=list)
    allow: list[Path] = Field(default_factory=list)


def canonicalize_dir(path: Path, workspace_root: Path) -> Path:
    """Resolve `path` to an absolute, symlink- and `..`-canonicalized form. This is the single
    canonicalization primitive for every path-shaped value in the permissions system —
    `DirectoryAccessTable` uses it for `readDirs`/`writeDirs` rule paths below, and
    `klorb.permissions.workspace.canonicalize_candidate` calls it directly (rather than
    duplicating the algorithm) so a model-supplied tool-call `filename` is canonicalized
    identically to a config-file rule path.

    A leading `~`/`~user` component is expanded first (`Path.expanduser()`), so `"~/.ssh"`
    means the invoking user's home directory regardless of `workspace_root` — this must happen
    before the relative-path check below, since an unexpanded `~...` path is not absolute and
    would otherwise be (wrongly) joined onto `workspace_root` as a literal `~` subdirectory.
    After that, a still-relative `path` is joined onto `workspace_root` first, so a rule like
    `Allow("..")` means the same thing as `Allow("<workspace_root>/..")`, not whatever the
    process's current working directory happens to be. `workspace_root` itself is resolved
    first, so this is well-defined even if the caller passes it unresolved.

    Note (TOCTOU): the returned path is canonical as of this call, not guaranteed to still
    point at the same target by the time a caller actually performs I/O on it — see
    `klorb.permissions.workspace.canonicalize_candidate`'s docstring.
    """
    path = path.expanduser()
    if not path.is_absolute():
        path = workspace_root.resolve(strict=False) / path
    return path.resolve(strict=False)


def privileged_dirs(workspace_root: Path, approved_scopes: set[str] | None = None) -> list[Path]:
    """Canonicalized list of every directory klorb's file tools must never read from or write to:
    the workspace's own `${workspace_root}/.klorb/` project dir, plus the process-wide
    `KLORB_CONFIG_DIR`/`KLORB_DATA_DIR`/`KLORB_STATE_DIR` from `klorb.paths` (resolved here,
    not cached at import time, so an env-var override picked up by `klorb.paths` is honored).

    This is the single source of truth `klorb.permissions.workspace.evaluate_write` and
    `resolve_and_evaluate_read` both check against, unconditionally, ahead of the
    `writeDirs`/`readDirs` tables — no `allow` entry in either table can re-enable access to
    anything this list contains.

    `approved_scopes` is the set of session-only escalation scopes the user has granted this
    session (see `SessionConfig.approved_scopes`, populated by the `EscalatePrivileges` tool).
    A `"workspace"` scope in it omits `${workspace_root}/.klorb/` from the returned list,
    lifting the privileged-path deny on that one directory for the rest of the session — the
    process-wide `KLORB_*_DIR` locations stay privileged regardless, since `"workspace"` scope
    only ever covers the workspace's own project dir. A `"homedir"` scope in it omits
    `KLORB_CONFIG_DIR`, `KLORB_DATA_DIR` and `KLORB_STATE_DIR`, lifting the privileged-path deny
    on those directories for the rest of the session.
    `None` (or an empty set) preserves the pre-escalation behavior: every privileged dir is denied.
    """
    root = workspace_root.resolve(strict=False)
    dirs: list[Path] = []
    if not (approved_scopes and "homedir" in approved_scopes):
        dirs.append(KLORB_DATA_DIR.resolve(strict=False))
        dirs.append(KLORB_STATE_DIR.resolve(strict=False))
        dirs.append(KLORB_CONFIG_DIR.resolve(strict=False))
    if not (approved_scopes and "workspace" in approved_scopes):
        dirs.append(root / KLORB_PROJECT_DIR_NAME)
    return dirs


def is_privileged_path(
    path: Path, workspace_root: Path, approved_scopes: set[str] | None = None,
) -> bool:
    """Return whether `path` (already canonicalized by the caller) is one of, or falls beneath,
    any directory in `privileged_dirs(workspace_root, approved_scopes)` — the same
    equal-or-descendant containment semantics `DirectoryAccessTable._matches` uses for ordinary
    rules. See `privileged_dirs` for how `approved_scopes` gates the workspace `.klorb/` dir."""
    return any(path == d or path.is_relative_to(d) for d in privileged_dirs(workspace_root, approved_scopes))


def workspace_klorb_dir(workspace_root: Path) -> Path:
    """Return the canonicalized `${workspace_root}/.klorb/` path — the one privileged dir an
    `EscalatePrivileges(scope="workspace")` grant can unlock. Used by
    `klorb.permissions.workspace` to distinguish a workspace-`.klorb` deny (which should point the
    agent at the `EscalatePrivileges` tool) from a process-wide `KLORB_*_DIR` deny (which it can't)."""
    return workspace_root.resolve(strict=False) / KLORB_PROJECT_DIR_NAME


def is_under_workspace_klorb_dir(path: Path, workspace_root: Path) -> bool:
    """Return whether `path` (already canonicalized) is the workspace `.klorb/` dir or beneath it
    — the subset of `is_privileged_path` an `EscalatePrivileges` \"workspace\" scope grant lifts."""
    klorb = workspace_klorb_dir(workspace_root)
    return path == klorb or path.is_relative_to(klorb)


def is_under_homedir_klorb_dir(path: Path) -> bool:
    """Return whether `path` (already canonicalized) is KLORB_DATA_DIR, KLORB_STATE_DIR,
    or KLORB_CONFIG_DIR (or beneath them) — the subset of `is_privileged_path` an
    `EscalatePrivileges` \"homedir\" scope grant lifts."""
    return (
        path == KLORB_DATA_DIR.resolve(strict=False)
        or path.is_relative_to(KLORB_DATA_DIR.resolve(strict=False))
        or path == KLORB_STATE_DIR.resolve(strict=False)
        or path.is_relative_to(KLORB_STATE_DIR.resolve(strict=False))
        or path == KLORB_CONFIG_DIR.resolve(strict=False)
        or path.is_relative_to(KLORB_CONFIG_DIR.resolve(strict=False))
    )


class DirectoryAccessTable(PermissionsTable[Path]):
    """A `PermissionsTable` over canonicalized directory paths: a rule matches a candidate if
    the rule's directory is the candidate itself or an ancestor of it (containment)."""

    def __init__(self, rules: DirRules, workspace_root: Path) -> None:
        super().__init__(
            deny=[canonicalize_dir(path, workspace_root) for path in rules.deny],
            ask=[canonicalize_dir(path, workspace_root) for path in rules.ask],
            allow=[canonicalize_dir(path, workspace_root) for path in rules.allow],
        )

    def _matches(self, rule: Path, candidate: Path) -> bool:
        return candidate == rule or candidate.is_relative_to(rule)


def find_workspace_root(cwd: Path) -> Path:
    """Search `cwd` and its ancestors for the nearest directory containing an immediate-child
    `.klorb` directory that is not itself a symlink, returning it as the workspace root. A
    disqualified candidate (a symlinked `.klorb`, or a `.klorb` that's a plain file rather than
    a directory) does not stop the search — it keeps walking upward. Falls back to `cwd` itself
    (canonicalized) if no ancestor qualifies.
    """
    current = cwd.resolve(strict=False)
    for candidate in (current, *current.parents):
        marker = candidate / KLORB_PROJECT_DIR_NAME
        if marker.is_dir() and not marker.is_symlink():
            return candidate
    return current


_tempdirs_to_remove_on_exit: list[Path] = []
"""Directories `create_tempdir_for_session(..., remove_on_exit=True)` has registered for
`atexit` cleanup -- module-global since the `atexit` handler itself is registered once, the
first time any caller opts in, and needs a single shared list to sweep."""


def _remove_registered_tempdirs() -> None:
    """`atexit` handler for `_tempdirs_to_remove_on_exit`: best-effort `shutil.rmtree()`s every
    directory in it, ignoring one a caller already removed itself."""
    for directory in _tempdirs_to_remove_on_exit:
        logger.debug("atexit: removing registered scratch tempdir %s", directory)
        shutil.rmtree(directory, ignore_errors=True)


def create_tempdir_for_session(
    session_config: "SessionConfig", *, mode: Literal["r", "w"] = "r", remove_on_exit: bool = False,
) -> Path:
    """Create a fresh `tempfile.mkdtemp()` directory, grant `session_config` `readDirs` access
    to it (a new `DirRules` with the directory's canonicalized path appended to `allow`,
    replacing `session_config.read_dirs` rather than mutating its `allow` list in place -- see
    `DirRules`'s immutable-after-construction contract), and return the canonicalized path.
    General directory/permissions plumbing for "mint a scratch location for an agent session" —
    e.g. `klorb.evals.run_evals`'s `--self-review` review session, which needs to read a copy of
    the eval log.

    `mode="w"` also grants `writeDirs` access to the same directory (a matching new `DirRules`
    replacing `session_config.write_dirs`) — `"w"` implies read access too, since a directory a
    session can write to but not read back is rarely useful. The default, `"r"`, grants read
    access only.

    `remove_on_exit`, when `True`, registers the directory for automatic `shutil.rmtree()`
    cleanup at process exit (an `atexit` handler is registered once, the first time any caller
    opts in) instead of leaving cleanup to the caller — the default, `False`, matches every
    existing inline `DirRules(allow=[...])` grant site, which owns its own cleanup.
    """
    workspace_root = session_config.workspace.path
    directory = canonicalize_dir(Path(tempfile.mkdtemp()), workspace_root)
    logger.debug("Created scratch tempdir %s (mode=%r)", directory, mode)
    session_config.read_dirs = DirRules(
        deny=session_config.read_dirs.deny, ask=session_config.read_dirs.ask,
        allow=[*session_config.read_dirs.allow, directory])
    if mode == "w":
        session_config.write_dirs = DirRules(
            deny=session_config.write_dirs.deny, ask=session_config.write_dirs.ask,
            allow=[*session_config.write_dirs.allow, directory])
    if remove_on_exit:
        logger.debug("Registering scratch tempdir %s for atexit cleanup", directory)
        if not _tempdirs_to_remove_on_exit:
            atexit.register(_remove_registered_tempdirs)
        _tempdirs_to_remove_on_exit.append(directory)
    return directory
