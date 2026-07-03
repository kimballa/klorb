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

from pathlib import Path

from pydantic import BaseModel, Field

from klorb.paths import KLORB_CONFIG_DIR, KLORB_DATA_DIR, KLORB_STATE_DIR
from klorb.permissions.table import PermissionsTable

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


def privileged_dirs(workspace_root: Path) -> list[Path]:
    """Canonicalized list of every directory klorb's file tools must never read from or write to:
    the workspace's own `${workspace_root}/.klorb/` project dir, plus the process-wide
    `KLORB_CONFIG_DIR`/`KLORB_DATA_DIR`/`KLORB_STATE_DIR` from `klorb.paths` (resolved here,
    not cached at import time, so an env-var override picked up by `klorb.paths` is honored).

    This is the single source of truth `klorb.permissions.workspace.evaluate_write` and
    `resolve_and_evaluate_read` both check against, unconditionally, ahead of the
    `writeDirs`/`readDirs` tables — no `allow` entry in either table can re-enable access to
    anything this list contains.

    TODO(aaron): there is no way to grant a temporary, user-confirmed exception to this deny yet
    (e.g. for an agent that legitimately needs to inspect its own session state); every match
    here is an unconditional, permanent "deny" until such a mechanism is built.
    """
    root = workspace_root.resolve(strict=False)
    return [
        root / KLORB_PROJECT_DIR_NAME,
        KLORB_CONFIG_DIR.resolve(strict=False),
        KLORB_DATA_DIR.resolve(strict=False),
        KLORB_STATE_DIR.resolve(strict=False),
    ]


def is_privileged_path(path: Path, workspace_root: Path) -> bool:
    """Return whether `path` (already canonicalized by the caller) is one of, or falls beneath,
    any directory in `privileged_dirs(workspace_root)` — the same equal-or-descendant
    containment semantics `DirectoryAccessTable._matches` uses for ordinary rules."""
    return any(path == d or path.is_relative_to(d) for d in privileged_dirs(workspace_root))


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
