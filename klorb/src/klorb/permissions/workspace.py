# © Copyright 2026 Aaron Kimball
"""Resolves a filename argument supplied by a model tool call into a canonical on-disk path,
and evaluates that path against the `readFiles`/`writeFiles` `FileAccessTable`s (an exact-match
check, consulted first), then the workspace-root boundary and the `readDirs`/`writeDirs`
`DirectoryAccessTable`s, to decide whether the operation is allowed. See
docs/specs/permissions.md, docs/adrs/confine-file-tools-to-workspace-root.md, and
docs/adrs/gate-read-hard-boundary-on-workspace-trust.md.

This module takes a `ToolSetupContext` parameter purely as a type annotation (never
instantiates or introspects it beyond attribute access), so the import is `TYPE_CHECKING`-only
— a real import here would cycle, since `ToolSetupContext` pulls in `klorb.session`, which
itself depends on `klorb.permissions.directory_access` for `DirRules` and
`klorb.permissions.file_access` for `FileRules`. `klorb.tools`' own file tools import from this
module, not the other way around.

Note (TOCTOU): every path this module returns is canonical only as of the moment it's resolved
— nothing here holds an open OS-level handle across the gap between a permission check and the
caller's actual file I/O, so a directory rename/symlink swap in that window could redirect an
approved operation to an unintended target.

TODO(aaron): close the TOCTOU gap above by operating relative to an `os.open()`-obtained
directory file descriptor (e.g. with `O_NOFOLLOW`/`O_DIRECTORY`) rather than re-resolving a
path string.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from klorb.permissions.directory_access import (
    DirectoryAccessTable,
    canonicalize_dir,
    is_privileged_path,
    is_under_workspace_klorb_dir,
)
from klorb.permissions.file_access import FileAccessTable
from klorb.permissions.table import Verdict, stricter_verdict

if TYPE_CHECKING:
    from klorb.tools.setup_context import ToolSetupContext


def _approved_scopes(context: "ToolSetupContext") -> set[str] | None:
    """The session-only privilege-escalation scopes approved this process, pulled off the
    `ToolSetupContext`'s `ProcessConfig` (see `ProcessConfig.approved_scopes`, populated by the
    `EscalatePrivileges` tool). `None` when the context carries no `ProcessConfig` (most unit
    tests), so `is_privileged_path` sees no approved scopes and keeps every privileged dir
    denied, exactly as before escalation existed."""
    process_config = context.process_config
    return process_config.approved_scopes if process_config is not None else None


def _privileged_deny(path: Path, workspace_root: Path, *, is_write: bool) -> Verdict:
    """Return the verdict for a path that failed `is_privileged_path`: always `\"deny\"`, but with a
    side effect \u2014 when the path is the workspace's own `.klorb/` dir or beneath it, raise a
    `PermissionError` whose message points the agent at the `EscalatePrivileges` tool (the one
    mechanism that can lift this deny for the rest of the session), instead of letting the generic
    `\"Permission denied: <op> <path>\"` reach the model with no hint. A process-wide `KLORB_*_DIR`
    deny stays a plain `\"deny\"` verdict: `EscalatePrivileges` can't unlock those, so pointing the
    agent at the tool there would only waste a round trip."""
    if is_under_workspace_klorb_dir(path, workspace_root):
        op = "write to" if is_write else "read"
        raise PermissionError(
            f"Permission denied: {op} {path}. This file is inside the workspace's privileged "
            f".klorb/ directory, which is hard-blocked from direct tool access. If you need to "
            f"read or write files there, call the EscalatePrivileges tool with scope \"workspace\" "
            f"to request temporary, session-only access; once the user approves, retry the "
            f"operation.")
    return "deny"


def canonicalize_candidate(context: "ToolSetupContext", filename: str) -> Path:
    """Resolve `filename` to an absolute, symlink- and `..`-canonicalized path via
    `klorb.permissions.directory_access.canonicalize_dir` (a leading `~`/`~user` is expanded to
    the invoking user's home directory; a still-relative result is joined onto
    `context.session_config.workspace.path`). Does NOT check the result is inside any
    particular boundary — callers that need a hard boundary should use
    `resolve_within_workspace()` instead; callers that only need canonicalization ahead of a
    permissions-table check (e.g. a trusted-workspace `ReadFile`) call this directly.
    """
    root = context.session_config.workspace.path.resolve()
    return canonicalize_dir(Path(filename), root)


def resolve_within_workspace(context: "ToolSetupContext", filename: str) -> Path:
    """Resolve `filename` (via `canonicalize_candidate()`) and verify the result is
    `context.session_config.workspace.path` or somewhere beneath it.

    This means a symlink hop or a `../../` traversal can't be used to reach a path outside the
    workspace root even though the check itself only compares final, resolved paths.

    Raises `PermissionError` if the resolved path falls outside the workspace root.
    """
    root = context.session_config.workspace.path.resolve()
    resolved = canonicalize_candidate(context, filename)

    if not resolved.is_relative_to(root):
        raise PermissionError(
            f"{filename!r} resolves to {resolved}, which is outside the permitted "
            f"workspace root {root}")

    return resolved


def _normalize_for_write(verdict: Verdict | None) -> Verdict:
    """A table returning `None` for a candidate means that table has no opinion on it. For a
    write interrogation specifically, "no opinion" is normalized to `"ask"` rather than a
    permissive default — write access is granted only where something explicitly said `allow`,
    never merely because nothing said `deny`. (This does not apply to a bare read interrogation;
    see `resolve_and_evaluate_read()`, which keeps its own, more permissive, no-match fallback.)
    """
    return verdict if verdict is not None else "ask"


def evaluate_write(context: "ToolSetupContext", path: Path) -> Verdict:
    """Evaluate a write to the already-workspace-confined `path` (the caller must already have
    called `resolve_within_workspace()` to obtain it).

    Checks `klorb.permissions.directory_access.is_privileged_path()` first — unconditional, not
    part of either table, so no `allow` entry (even one covering the whole workspace) can
    re-enable access. Writing there is where klorb's own config/session state lives; letting the
    agent freely rewrite its own permission grants would let it silently escalate its own access.

    Next, checks `context.permission_override`: if it's set and `path` is a member of its
    `paths`, returns `"allow"` without consulting either table — a one-shot "Allow (once)"
    bypass (see `ToolSetupContext.permission_override`) that never overrides the
    privileged-path deny above.

    Otherwise, evaluates `path` against both `readDirs` and `writeDirs` independently and takes
    the stricter of the two (each normalized via `_normalize_for_write()` first): write access
    can never be more permissive than read access for the same path, so granting write requires
    an explicit `allow` in *both* tables, not just `writeDirs`. This is deliberately not a
    fallback to `writeDirs` alone — a path `writeDirs` never mentions is `"ask"`, not `"allow"`,
    even if `readDirs` allows it.
    """
    workspace_root = context.session_config.workspace.path.resolve()
    if is_privileged_path(path, workspace_root, _approved_scopes(context)):
        return _privileged_deny(path, workspace_root, is_write=True)
    if context.permission_override is not None and path in context.permission_override.paths:
        return "allow"

    read_table = DirectoryAccessTable(context.session_config.read_dirs, workspace_root)
    write_table = DirectoryAccessTable(context.session_config.write_dirs, workspace_root)
    return stricter_verdict(
        _normalize_for_write(read_table.evaluate(path)),
        _normalize_for_write(write_table.evaluate(path)))


def resolve_and_evaluate_read(context: "ToolSetupContext", filename: str) -> tuple[Path, Verdict]:
    """Resolve `filename` and evaluate it against `readFiles` (exact match) then `readDirs`
    (never `writeDirs`/`writeFiles` — a write grant does not imply a read grant; see
    `evaluate_write()` for the converse).

    `filename` is canonicalized via `canonicalize_candidate()` first, with no boundary check
    yet. `klorb.permissions.directory_access.is_privileged_path()` is checked immediately
    against that candidate — unconditional, before even `readFiles`, so no `readFiles.allow`
    entry can grant access to `${workspace_root}/.klorb/` or the process-wide
    `KLORB_CONFIG_DIR`/`KLORB_DATA_DIR`/`KLORB_STATE_DIR` locations. Next, a `FileAccessTable`
    built from `context.session_config.read_files` is checked against the same candidate: if it
    has any opinion at all (`FileAccessTable.evaluate()` returns non-`None`), that verdict is
    returned as-is — no workspace-root boundary check and no `readDirs` fallback are ever
    reached for that candidate. This is deliberate: an exact `readFiles` entry must be able to
    name a path outside `workspace_root` (e.g. a special device file — see
    `klorb.resources.default-config.json`) and still resolve to a verdict, rather than being
    rejected by the boundary check below before `readFiles` ever gets a say.

    If `readFiles` has no opinion, this falls through to the existing `readDirs` behavior,
    branching on `context.session_config.workspace.trusted`:

    - Untrusted (default — see `SessionConfig.workspace`): resolves via
      `resolve_within_workspace()`, which raises `PermissionError` if the result falls outside
      `workspace_root`, exactly like the write tools. `readDirs` is then evaluated on that
      already-in-workspace path, falling back to `"allow"` if nothing matches — unlike
      `evaluate_write()`, this fallback is unaffected by `writeDirs` (a hard boundary already
      confines the candidate, so a permissive read default here doesn't compromise the
      write-side invariant).
    - Trusted (set via the interactive workspace-trust flow — see
      `klorb.workspace`/docs/specs/projects-and-trust.md): resolves via
      `canonicalize_candidate()`, with no boundary raise, so `readDirs.allow` can grant access
      outside `workspace_root`. Falls back to `"ask"` if nothing matches — no implicit
      "inside the workspace" default is applied in this mode, and no implicit "outside the
      workspace" deny either; a path `readDirs` has no opinion on reaches the same interactive
      confirmation flow `evaluate_write()`'s own no-match fallback uses, rather than being
      refused outright. Default *allow* grants in this mode still come from an explicit
      `readDirs.allow` entry `klorb.workspace.workspace_init.write_initial_project_config()`
      writes into `.klorb/klorb-config.json`, not from a rule baked into this evaluator.

    `context.permission_override` is then checked, exactly as in `evaluate_write()`: `path`
    being a member of its `paths` short-circuits to `"allow"` without consulting `readDirs`.

    Returns `(path, verdict)` so the caller can enforce the verdict and then open the same
    canonicalized path that was actually checked.
    """
    workspace_root = context.session_config.workspace.path.resolve()
    candidate = canonicalize_candidate(context, filename)

    if is_privileged_path(candidate, workspace_root, _approved_scopes(context)):
        return candidate, _privileged_deny(candidate, workspace_root, is_write=False)

    file_table = FileAccessTable(context.session_config.read_files, workspace_root)
    file_verdict = file_table.evaluate(candidate)
    if file_verdict is not None:
        return candidate, file_verdict

    if context.session_config.workspace.trusted:
        path = candidate
        fallback: Verdict = "ask"
    else:
        path = resolve_within_workspace(context, filename)
        fallback = "allow"

    if context.permission_override is not None and path in context.permission_override.paths:
        return path, "allow"

    read_table = DirectoryAccessTable(context.session_config.read_dirs, workspace_root)
    verdict = read_table.evaluate(path)
    if verdict is None:
        verdict = fallback
    return path, verdict


def resolve_and_evaluate_write(context: "ToolSetupContext", filename: str) -> tuple[Path, Verdict]:
    """Resolve `filename` and decide whether writing to it is allowed, consulting `writeFiles`
    (exact match) before the workspace-root boundary and `readDirs`/`writeDirs` `evaluate_write()`
    applies.

    `filename` is canonicalized via `canonicalize_candidate()` first, with no boundary check
    yet. `is_privileged_path()` is checked immediately against that candidate — unconditional,
    before even `writeFiles`, exactly as in `resolve_and_evaluate_read()`. Next, a
    `FileAccessTable` built from `context.session_config.write_files` is checked: if it has any
    opinion at all, that verdict is returned as-is, with no workspace-root boundary check and no
    `evaluate_write()` fallback — the same rationale as `resolve_and_evaluate_read()`'s own
    `readFiles` short-circuit (see its docstring), letting an exact `writeFiles` entry name a
    path outside `workspace_root`.

    If `writeFiles` has no opinion, this falls through to the existing behavior exactly:
    `resolve_within_workspace()` (raising `PermissionError` if the candidate falls outside
    `workspace_root`) followed by `evaluate_write()`'s `readDirs`/`writeDirs` merge.

    Returns `(path, verdict)` so the caller can enforce the verdict and then write to the same
    canonicalized path that was actually checked.
    """
    workspace_root = context.session_config.workspace.path.resolve()
    candidate = canonicalize_candidate(context, filename)

    if is_privileged_path(candidate, workspace_root, _approved_scopes(context)):
        return candidate, _privileged_deny(candidate, workspace_root, is_write=True)

    file_table = FileAccessTable(context.session_config.write_files, workspace_root)
    file_verdict = file_table.evaluate(candidate)
    if file_verdict is not None:
        return candidate, file_verdict

    path = resolve_within_workspace(context, filename)
    return path, evaluate_write(context, path)
