# © Copyright 2026 Aaron Kimball
"""Directory access control: concrete `PermissionsTable` resource kind governing
which directories klorb's file tools may read from and write to."""

import atexit
import logging
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from klorb.paths import get_klorb_config_dir, get_klorb_data_dir, get_klorb_state_dir
from klorb.permissions.table import PermissionsTable

if TYPE_CHECKING:
    # SessionConfig pulls in this module (for DirRules), so the import stays TYPE_CHECKING-only
    # to avoid a cycle.
    from klorb.session import SessionConfig

logger = logging.getLogger(__name__)

KLORB_PROJECT_DIR_NAME = ".klorb"
"""Name of the directory (immediate child of a workspace root) that holds klorb's own
per-project config and state."""

CLAUDE_PROJECT_DIR_NAME = ".claude"
"""Workspace-root child directory whose `skills/` subtree is discovered as a second
`workspace`-namespace skill source when `compatibility.claudeSkills` is enabled."""

SKILLS_DIRNAME = "skills"
"""Directory name holding skill subdirectories within a tier's parent directory."""


class DirRules(BaseModel):
    """One directory-access direction's (`readDirs` or `writeDirs`) `deny`/`ask`/`allow`
    rule lists, as plain path data. Treated as immutable after construction: nothing in
    this codebase mutates these lists in place post-construction, so
    `SessionConfig.model_copy()`'s shallow copy sharing the underlying list objects across
    a template and its cloned sessions is harmless.
    """

    deny: list[Path] = Field(default_factory=list)
    ask: list[Path] = Field(default_factory=list)
    allow: list[Path] = Field(default_factory=list)


def concat_dir_rules(base: DirRules, addition: DirRules) -> DirRules:
    """Concatenate `addition`'s deny/ask/allow entries onto `base`'s own, per category."""
    return DirRules(
        deny=list(base.deny) + list(addition.deny),
        ask=list(base.ask) + list(addition.ask),
        allow=list(base.allow) + list(addition.allow),
    )


def canonicalize_dir(path: Path, workspace_root: Path) -> Path:
    """Resolve `path` to an absolute, symlink- and `..`-canonicalized form. This is the single
    canonicalization primitive for every path-shaped value in the permissions system.

    A leading `~`/`~user` component is expanded first (`Path.expanduser()`), so `"~/.ssh"`
    means the invoking user's home directory regardless of `workspace_root`.
    After that, a still-relative `path` is joined onto `workspace_root` first, so a rule like
    `Allow("..")` means the same thing as `Allow("<workspace_root>/..")`, not whatever the
    process's current working directory happens to be. `workspace_root` itself is resolved
    first, so this is well-defined even if the caller passes it unresolved.

    Note (TOCTOU): the returned path is canonical as of this call, not guaranteed to still
    point at the same target by the time a caller actually performs I/O on it.
    """
    path = path.expanduser()
    if not path.is_absolute():
        path = workspace_root.resolve(strict=False) / path
    return path.resolve(strict=False)


def privileged_dirs(
    workspace_root: Path, approved_scopes: set[str] | None = None, *, claude_skills_compat: bool = False,
) -> list[Path]:
    """Canonicalized list of every directory klorb's file tools must never read from or write to:
    the workspace's own `${workspace_root}/.klorb/` project dir, plus the process-wide
    `get_klorb_config_dir()`/`get_klorb_data_dir()`/`get_klorb_state_dir()` from `klorb.paths`
    (resolved at call time, so an env-var override picked up by `klorb.paths` is honored).
    When `claude_skills_compat` is true, `${workspace_root}/.claude/skills/` joins the workspace
    `.klorb/` dir.

    This is the single source of truth `klorb.permissions.workspace.evaluate_write` and
    `resolve_and_evaluate_read` both check against, unconditionally, ahead of the
    `writeDirs`/`readDirs` tables.

    `approved_scopes` is the set of session-only escalation scopes the user has granted this
    session. A `"workspace"` scope in it omits `${workspace_root}/.klorb/` and (when `claude_skills_compat`)
    `${workspace_root}/.claude/skills/` from the returned list, lifting the privileged-path deny on
    those directories for the rest of the session. A `"homedir"` scope in it omits
    `KLORB_CONFIG_DIR`, `KLORB_DATA_DIR` and `KLORB_STATE_DIR`, lifting the
    privileged-path deny on those directories for the rest of the
    session. `None` (or an empty set) preserves the pre-escalation behavior: every privileged dir
    is denied.
    """
    root = workspace_root.resolve(strict=False)
    dirs: list[Path] = []
    if not (approved_scopes and "homedir" in approved_scopes):
        dirs.append(get_klorb_data_dir().resolve(strict=False))
        dirs.append(get_klorb_state_dir().resolve(strict=False))
        dirs.append(get_klorb_config_dir().resolve(strict=False))
    if not (approved_scopes and "workspace" in approved_scopes):
        dirs.append(root / KLORB_PROJECT_DIR_NAME)
        if claude_skills_compat:
            dirs.append(root / CLAUDE_PROJECT_DIR_NAME / SKILLS_DIRNAME)
    return dirs


def is_privileged_path(
    path: Path, workspace_root: Path, approved_scopes: set[str] | None = None, *,
    claude_skills_compat: bool = False,
) -> bool:
    """Return whether `path` (already canonicalized by the caller) is one of, or falls beneath,
    any directory in `privileged_dirs(workspace_root, approved_scopes, claude_skills_compat=...)`."""
    return any(
        path == d or path.is_relative_to(d)
        for d in privileged_dirs(workspace_root, approved_scopes, claude_skills_compat=claude_skills_compat))


def workspace_klorb_dir(workspace_root: Path) -> Path:
    """Return the canonicalized `${workspace_root}/.klorb/` path."""
    return workspace_root.resolve(strict=False) / KLORB_PROJECT_DIR_NAME


def is_under_workspace_klorb_dir(path: Path, workspace_root: Path) -> bool:
    """Return whether `path` (already canonicalized) is the workspace `.klorb/` dir or beneath it."""
    klorb = workspace_klorb_dir(workspace_root)
    return path == klorb or path.is_relative_to(klorb)


def workspace_claude_skills_dir(workspace_root: Path) -> Path:
    """Return the canonicalized `${workspace_root}/.claude/skills/` path."""
    return workspace_root.resolve(strict=False) / CLAUDE_PROJECT_DIR_NAME / SKILLS_DIRNAME


def is_under_workspace_claude_skills_dir(path: Path, workspace_root: Path) -> bool:
    """Return whether `path` (already canonicalized) is the workspace `.claude/skills/` dir or
    beneath it."""
    claude_skills = workspace_claude_skills_dir(workspace_root)
    return path == claude_skills or path.is_relative_to(claude_skills)


def is_under_homedir_klorb_dir(path: Path) -> bool:
    """Return whether `path` (already canonicalized) is get_klorb_data_dir(), get_klorb_state_dir(),
    or get_klorb_config_dir() (or beneath them)."""
    return (
        path == get_klorb_data_dir().resolve(strict=False)
        or path.is_relative_to(get_klorb_data_dir().resolve(strict=False))
        or path == get_klorb_state_dir().resolve(strict=False)
        or path.is_relative_to(get_klorb_state_dir().resolve(strict=False))
        or path == get_klorb_config_dir().resolve(strict=False)
        or path.is_relative_to(get_klorb_config_dir().resolve(strict=False))
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
    a directory) does not stop the search. Falls back to `cwd` itself
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
`atexit` cleanup."""


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
    to it, and return the canonicalized path.
    General directory/permissions plumbing for "mint a scratch location for an agent session".

    `mode="w"` also grants `writeDirs` access to the same directory (a matching new `DirRules`
    replacing `session_config.write_dirs`). The default, `"r"`, grants read
    access only.

    `remove_on_exit`, when `True`, registers the directory for automatic `shutil.rmtree()`
    cleanup at process exit instead of leaving cleanup to the caller.
    """
    access = session_config.workspace_access_snapshot()
    workspace_root = access.workspace.path
    directory = canonicalize_dir(Path(tempfile.mkdtemp()), workspace_root)
    logger.debug("Created scratch tempdir %s (mode=%r)", directory, mode)
    new_read_dirs = DirRules(
        deny=access.read_dirs.deny, ask=access.read_dirs.ask,
        allow=[*access.read_dirs.allow, directory])
    new_write_dirs = (
        DirRules(
            deny=access.write_dirs.deny, ask=access.write_dirs.ask,
            allow=[*access.write_dirs.allow, directory])
        if mode == "w" else access.write_dirs)
    session_config.apply_workspace_access(
        workspace=access.workspace, read_dirs=new_read_dirs, write_dirs=new_write_dirs)
    if remove_on_exit:
        logger.debug("Registering scratch tempdir %s for atexit cleanup", directory)
        if not _tempdirs_to_remove_on_exit:
            atexit.register(_remove_registered_tempdirs)
        _tempdirs_to_remove_on_exit.append(directory)
    return directory
