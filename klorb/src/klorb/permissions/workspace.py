# © Copyright 2026 Aaron Kimball
"""Resolves a filename argument supplied by a model tool call into a canonical on-disk path,
and evaluates that path against the workspace-root boundary and the `readDirs`/`writeDirs`
`DirectoryAccessTable`s to decide whether the operation is allowed. See
docs/specs/permissions.md, docs/adrs/confine-file-tools-to-workspace-root.md, and
docs/adrs/gate-read-hard-boundary-on-workspace-trust.md.

This module takes a `ToolSetupContext` parameter purely as a type annotation (never
instantiates or introspects it beyond attribute access), so the import is `TYPE_CHECKING`-only
— a real import here would cycle, since `ToolSetupContext` pulls in `klorb.session`, which
itself depends on `klorb.permissions.directory_access` for `DirRules`. `klorb.tools`' own file
tools import from this module, not the other way around.

Note (TOCTOU): every path this module returns is canonical only as of the moment it's resolved
— nothing here holds an open OS-level handle across the gap between a permission check and the
caller's actual file I/O, so a directory rename/symlink swap in that window could redirect an
approved operation to an unintended target. Closing this would require operating relative to an
`os.open()`-obtained directory file descriptor (e.g. with `O_NOFOLLOW`/`O_DIRECTORY`) rather
than re-resolving a path string. Not implemented — see TODO.md's "Permissions" backlog item.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from klorb.permissions.directory_access import KLORB_PROJECT_DIR_NAME, DirectoryAccessTable
from klorb.permissions.table import Verdict

if TYPE_CHECKING:
    from klorb.tools.setup_context import ToolSetupContext


def canonicalize_candidate(context: "ToolSetupContext", filename: str) -> Path:
    """Resolve `filename` to an absolute, symlink- and `..`-canonicalized path, joining it onto
    `context.session_config.workspace_root` first if relative. Does NOT check the result is
    inside any particular boundary — callers that need a hard boundary should use
    `resolve_within_workspace()` instead; callers that only need canonicalization ahead of a
    permissions-table check (e.g. a trusted-workspace `ReadFile`) call this directly.
    """
    root = context.session_config.workspace_root.resolve()
    candidate = Path(filename)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve(strict=False)


def resolve_within_workspace(context: "ToolSetupContext", filename: str) -> Path:
    """Resolve `filename` (via `canonicalize_candidate()`) and verify the result is
    `context.session_config.workspace_root` or somewhere beneath it.

    This means a symlink hop or a `../../` traversal can't be used to reach a path outside the
    workspace root even though the check itself only compares final, resolved paths.

    Raises `PermissionError` if the resolved path falls outside the workspace root.
    """
    root = context.session_config.workspace_root.resolve()
    resolved = canonicalize_candidate(context, filename)

    if not resolved.is_relative_to(root):
        raise PermissionError(
            f"{filename!r} resolves to {resolved}, which is outside the permitted "
            f"workspace root {root}")

    return resolved


def evaluate_write(context: "ToolSetupContext", path: Path) -> Verdict:
    """Evaluate a write to the already-workspace-confined `path` (the caller must already have
    called `resolve_within_workspace()` to obtain it) against `writeDirs`.

    Checks the hardcoded `${workspace_root}/.klorb/` implicit deny first — unconditional, not
    part of the `writeDirs` table, so no `writeDirs.allow` entry (even one covering the whole
    workspace) can re-enable it. Writing there is where klorb's own config/session state lives;
    letting the agent freely rewrite its own permission grants would let it silently escalate
    its own access. TODO: unlock path is a future `EscalatePrivileges` tool (see TODO.md).

    Falls back to `"allow"` when nothing in `writeDirs` matches, preserving the pre-existing,
    already-shipped write-tool default that anything inside the workspace is writable.
    """
    workspace_root = context.session_config.workspace_root.resolve()
    klorb_dir = workspace_root / KLORB_PROJECT_DIR_NAME
    if path == klorb_dir or path.is_relative_to(klorb_dir):
        return "deny"

    write_table = DirectoryAccessTable(context.session_config.write_dirs, workspace_root)
    return write_table.evaluate(path) or "allow"


def resolve_and_evaluate_read(context: "ToolSetupContext", filename: str) -> tuple[Path, Verdict]:
    """Resolve `filename` and evaluate it against `readDirs`/`writeDirs`, branching on
    `context.process_config.is_workspace_trusted`:

    - Untrusted (default, and the only state reachable today — see
      `ProcessConfig.is_workspace_trusted`): resolves via `resolve_within_workspace()`, which
      raises `PermissionError` if the result falls outside `workspace_root`, exactly like the
      write tools. The six-step chain below is then evaluated on that already-in-workspace
      path, falling back to `"allow"` if nothing matches (mirroring the write tools' zero-config
      default, since a hard boundary already confines the candidate).
    - Trusted (not reachable by any code path in this pass): resolves via
      `canonicalize_candidate()`, with no boundary raise, so `readDirs.allow` can grant access
      outside `workspace_root`. Falls back to `"deny"` if nothing matches — no implicit
      "inside the workspace" default is applied in this mode; default grants are meant to come
      from an explicit `readDirs.allow` entry a future project-bootstrap flow writes into
      `.klorb/klorb-config.json`, not from a rule baked into this evaluator.

    In both modes, the six-step chain is: `readDirs.deny -> readDirs.ask -> readDirs.allow ->
    writeDirs.deny -> writeDirs.ask -> writeDirs.allow` (the `readDirs` table is evaluated
    first; `writeDirs` is only consulted if `readDirs` doesn't match at all, so `readDirs.allow`
    can grant read-only access to something `writeDirs` denies).

    Returns `(path, verdict)` so the caller can enforce the verdict and then open the same
    canonicalized path that was actually checked.
    """
    workspace_root = context.session_config.workspace_root.resolve()
    if context.process_config.is_workspace_trusted:
        path = canonicalize_candidate(context, filename)
        fallback: Verdict = "deny"
    else:
        path = resolve_within_workspace(context, filename)
        fallback = "allow"

    read_table = DirectoryAccessTable(context.session_config.read_dirs, workspace_root)
    write_table = DirectoryAccessTable(context.session_config.write_dirs, workspace_root)
    verdict = read_table.evaluate(path)
    if verdict is None:
        verdict = write_table.evaluate(path)
    if verdict is None:
        verdict = fallback
    return path, verdict
