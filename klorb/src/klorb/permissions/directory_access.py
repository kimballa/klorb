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

from klorb.permissions.table import PermissionsTable

KLORB_PROJECT_DIR_NAME = ".klorb"
"""Name of the directory (immediate child of a workspace root) that holds klorb's own
per-project config and state. Shared with `klorb.process_config._project_config_path()`,
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
    """Resolve `path` to an absolute, symlink- and `..`-canonicalized form, matching
    `canonicalize_candidate`'s algorithm: a relative `path` is joined onto `workspace_root`
    first, so a rule like `Allow("..")` means the same thing as
    `Allow("<workspace_root>/..")`, not whatever the process's current working directory
    happens to be. `workspace_root` itself is resolved first, so this is well-defined even if
    the caller passes it unresolved.

    Note (TOCTOU): the returned path is canonical as of this call, not guaranteed to still
    point at the same target by the time a caller actually performs I/O on it — see
    `klorb.permissions.workspace.canonicalize_candidate`'s docstring.
    """
    if not path.is_absolute():
        path = workspace_root.resolve(strict=False) / path
    return path.resolve(strict=False)


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
