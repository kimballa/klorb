# © Copyright 2026 Aaron Kimball
"""Computes and persists permission grants for the interactive "ask" confirmation flow: what
gets added to `readDirs`/`writeDirs.allow` (an "Allow" decision) or `.deny` (a "Deny" decision),
and removed from `.ask` either way, in memory and on disk, when a user answers a
`PermissionAskRequired`/`MultiPermissionAskRequired` prompt with a persistent scope ("this
session", "this workspace", or "always for me"). See docs/specs/permissions.md's "Interactive
ask confirmation" section for the full design, including the granularity and cross-file rules
this module implements.

This module writes directly to `klorb-config.json` files via `pathlib`/`json` (through
`klorb.schema_envelope`), not through `CreateFileTool`/`EditFileTool` or any
`klorb.permissions` check — intentional: it's trusted harness code, not the agent, doing the
writing, in response to an explicit interactive user decision, exactly like a human hand-editing
the file. `${workspace_root}/.klorb/` and the `KLORB_*_DIR` locations stay unconditionally
privileged for the *agent's own tools* (see `klorb.permissions.directory_access.privileged_dirs`)
— that protection is unrelated to, and unaffected by, this module.
"""

from pathlib import Path
from typing import Any, Literal

from klorb.permissions.directory_access import DirectoryAccessTable, DirRules, canonicalize_dir
from klorb.process_config import (
    CONFIG_SCHEMA_NAME,
    CONFIG_SCHEMA_VERSION,
    SESSION_DEFAULTS_KEY,
    ProcessConfig,
    project_config_path,
    user_config_path,
)
from klorb.schema_envelope import read_versioned_json, write_versioned_json
from klorb.session import SessionConfig

GrantScope = Literal["session", "workspace", "homedir"]
"""Where a persistent decision is recorded. "once" isn't included here — it persists nothing,
and is handled entirely via `ToolSetupContext.permission_override`, never through this module."""

GrantAction = Literal["allow", "deny"]
"""Which rule category a persistent decision writes into."""

_READ_DIRS_KEY = "readDirs"
_WRITE_DIRS_KEY = "writeDirs"


def compute_grant_paths(
    read_dirs: DirRules, write_dirs: DirRules, workspace_root: Path, candidate: Path, is_write: bool,
) -> list[Path]:
    """The canonicalized directory (or directories) a grant for `candidate` should be recorded
    at: every `ask`-category rule's own path that matches `candidate` in `read_dirs` (always)
    unioned with `write_dirs` (only when `is_write` — a read-only access never considers
    `write_dirs`, matching `resolve_and_evaluate_read()`'s own read-only semantics), or, if
    nothing matched at all ("the dir was never mentioned"), `[candidate]` when `candidate` is
    itself a directory or `[candidate.parent]` when it's a file — see
    docs/adrs/grant-directory-candidate-at-itself-not-its-parent.md. This granularity computation
    is the same for an Allow or a Deny decision — only which rule category the grant writes into
    differs (see `GrantAction`).

    Pure and read-only — safe to call twice per grant (once by the TUI to render the modal's
    copy before the user picks a scope, once inside `apply_permission_grant` to actually mutate
    state), since nothing else touches this session's tables in between within one synchronous
    ask-and-apply flow.
    """
    read_table = DirectoryAccessTable(read_dirs, workspace_root)
    matched: list[Path] = list(read_table.matching_rules("ask", candidate))
    if is_write:
        write_table = DirectoryAccessTable(write_dirs, workspace_root)
        for rule in write_table.matching_rules("ask", candidate):
            if rule not in matched:
                matched.append(rule)
    if matched:
        return matched
    return [candidate] if candidate.is_dir() else [candidate.parent]


def _apply_decision_to_table(
    rules: DirRules, workspace_root: Path, granted_paths: list[Path], action: GrantAction,
) -> DirRules:
    """Return a NEW `DirRules`: `granted_paths` appended to `action`'s own list (`allow` or
    `deny`, deduped by canonical equality against that list's existing entries), and any `ask`
    entry that canonicalizes into `granted_paths` removed. The *other* category is left
    untouched: an "Allow, always" decision never strips an existing `deny` entry (which would be
    a security regression if a stricter admin-level deny already existed — `deny` still wins via
    category order regardless), and a "Deny, always" decision never strips an existing `allow`
    entry (the new `deny` entry already wins on its own). Never mutates `rules` in place, per
    `DirRules`'s documented immutable-by-convention contract.
    """
    granted_set = set(granted_paths)
    target = rules.allow if action == "allow" else rules.deny
    existing_target = {canonicalize_dir(p, workspace_root) for p in target}
    new_target = list(target)
    for granted in granted_paths:
        if granted not in existing_target:
            new_target.append(granted)
            existing_target.add(granted)
    new_ask = [p for p in rules.ask if canonicalize_dir(p, workspace_root) not in granted_set]
    if action == "allow":
        return DirRules(deny=list(rules.deny), ask=new_ask, allow=new_target)
    return DirRules(deny=new_target, ask=new_ask, allow=list(rules.allow))


def _dir_rules_from_json(raw: dict[str, Any]) -> DirRules:
    return DirRules(
        deny=[Path(p) for p in raw.get("deny", [])],
        ask=[Path(p) for p in raw.get("ask", [])],
        allow=[Path(p) for p in raw.get("allow", [])],
    )


def _dir_rules_to_json(rules: DirRules) -> dict[str, list[str]]:
    return {
        "deny": [str(p) for p in rules.deny],
        "ask": [str(p) for p in rules.ask],
        "allow": [str(p) for p in rules.allow],
    }


def _load_file_dir_rules(path: Path) -> tuple[dict[str, Any], DirRules, DirRules]:
    """Read `path`'s own raw `sessionDefaults.readDirs`/`writeDirs` (via `read_versioned_json`
    — `{}` if `path` doesn't exist), returned as `(full_raw_contents, read_dirs, write_dirs)`.
    `full_raw_contents` is returned alongside so the caller can write it back with only
    `readDirs`/`writeDirs` replaced, preserving every other key in the file untouched — this
    file's own lists only, never the in-memory merged `SessionConfig` that concatenates every
    config layer together.
    """
    raw = read_versioned_json(path, expected_schema_name=CONFIG_SCHEMA_NAME)
    session_defaults = raw.get(SESSION_DEFAULTS_KEY, {})
    read_dirs = _dir_rules_from_json(session_defaults.get(_READ_DIRS_KEY, {}))
    write_dirs = _dir_rules_from_json(session_defaults.get(_WRITE_DIRS_KEY, {}))
    return raw, read_dirs, write_dirs


def _write_file_dir_rules(
    path: Path, raw_contents: dict[str, Any], read_dirs: DirRules, write_dirs: DirRules,
) -> None:
    """Write `raw_contents` back to `path` with `sessionDefaults.readDirs`/`writeDirs` replaced
    by `read_dirs`/`write_dirs` (as path-string arrays), preserving every other key untouched.
    Creates `path`'s parent directory and a minimal schema envelope if `path` didn't exist yet
    (an empty `raw_contents` in that case, from `_load_file_dir_rules`).
    """
    session_defaults = dict(raw_contents.get(SESSION_DEFAULTS_KEY, {}))
    session_defaults[_READ_DIRS_KEY] = _dir_rules_to_json(read_dirs)
    session_defaults[_WRITE_DIRS_KEY] = _dir_rules_to_json(write_dirs)
    new_contents = dict(raw_contents)
    new_contents[SESSION_DEFAULTS_KEY] = session_defaults
    write_versioned_json(
        path, new_contents, schema_name=CONFIG_SCHEMA_NAME, schema_version=CONFIG_SCHEMA_VERSION)


def _apply_grant_to_file(
    path: Path, workspace_root: Path, granted_paths: list[Path], is_write: bool, action: GrantAction,
) -> None:
    """Record `granted_paths` into `path`'s own `readDirs` (always, removing any of its own
    matching `readDirs.ask` entries) and `writeDirs` (only when `is_write`, same removal rule) —
    into `action`'s category (`allow`/`deny`) of each — then write `path` back, auto-creating it
    if it doesn't exist yet.
    """
    raw, file_read_dirs, file_write_dirs = _load_file_dir_rules(path)
    new_read_dirs = _apply_decision_to_table(file_read_dirs, workspace_root, granted_paths, action)
    new_write_dirs = (
        _apply_decision_to_table(file_write_dirs, workspace_root, granted_paths, action) if is_write
        else file_write_dirs)
    _write_file_dir_rules(path, raw, new_read_dirs, new_write_dirs)


def _clean_ask_entries_only(path: Path, workspace_root: Path, granted_paths: list[Path]) -> None:
    """Best-effort: if `path` exists and its own `readDirs`/`writeDirs.ask` contains an entry
    canonicalizing into `granted_paths`, remove it (independently, from both tables) and write
    the file back — WITHOUT adding anything to either `allow`/`deny` list. A no-op if `path`
    doesn't exist, or if neither table has a matching `ask` entry (avoids a needless rewrite).
    Used only for the homedir-grant-may-clean-a-stale-workspace-ask asymmetry — see
    `apply_permission_grant`; never called the other direction.
    """
    if not path.is_file():
        return
    raw, file_read_dirs, file_write_dirs = _load_file_dir_rules(path)
    granted_set = set(granted_paths)
    new_read_ask = [p for p in file_read_dirs.ask if canonicalize_dir(p, workspace_root) not in granted_set]
    new_write_ask = [
        p for p in file_write_dirs.ask if canonicalize_dir(p, workspace_root) not in granted_set]
    if new_read_ask == file_read_dirs.ask and new_write_ask == file_write_dirs.ask:
        return
    new_read_dirs = DirRules(deny=file_read_dirs.deny, ask=new_read_ask, allow=file_read_dirs.allow)
    new_write_dirs = DirRules(deny=file_write_dirs.deny, ask=new_write_ask, allow=file_write_dirs.allow)
    _write_file_dir_rules(path, raw, new_read_dirs, new_write_dirs)


def apply_permission_grant(
    action: GrantAction,
    scope: GrantScope,
    session_config: SessionConfig,
    process_config: ProcessConfig | None,
    path: Path,
    is_write: bool,
) -> None:
    """Record a permanent Allow or Deny decision for `path` at `scope`, per
    docs/specs/permissions.md's "Interactive ask confirmation" section:

    - An `"allow"` decision always reassigns `session_config.read_dirs` (and `write_dirs`, when
      `is_write`) wholesale — never mutates their lists in place — so the live session sees the
      grant immediately; write access requires both tables to say `allow` (see
      docs/adrs/write-verdict-is-stricter-of-read-and-write-tables.md), which is why a write
      grant always promotes read too. A `"deny"` decision only touches the one table matching
      `is_write` (`write_dirs` for a write ask, `read_dirs` for a read ask) — denying a write
      doesn't imply denying a read that was never in question, and `write_dirs.deny` alone
      already forces the stricter-of-both-tables verdict to `"deny"` regardless of `read_dirs`.
    - `"session"` scope stops there: nothing else is touched.
    - `"workspace"`/`"homedir"` additionally reassign `process_config.session`'s matching
      table(s) the same way (so a future `/clear` in this process inherits the decision too) —
      skipped when `process_config` is `None` (a caller, e.g. `Session`, with no live
      `ProcessConfig` to ripple into) — and persist it to the scope's target file —
      `project_config_path(session_config.workspace.path)` for `"workspace"`,
      `user_config_path()` for `"homedir"` — auto-creating the file and its parent directory
      with a minimal schema envelope if it doesn't exist yet. The file-persist step doesn't
      depend on `process_config` at all (only `session_config.workspace.path`), so it still runs
      when `process_config` is `None`.
    - `"homedir"` only, additionally: best-effort-cleans a matching, now-redundant `ask` entry
      out of the *workspace* file if one independently exists there (removal only, never an
      addition) — the user's homedir-scope decision is the more-trusted, broader action. The
      reverse never happens: a `"workspace"` grant never opens the homedir (or `/etc`) file.
    """
    workspace_root = session_config.workspace.path.resolve()
    granted_paths = compute_grant_paths(
        session_config.read_dirs, session_config.write_dirs, workspace_root, path, is_write)

    touch_read = action == "allow" or not is_write
    touch_write = is_write

    if touch_read:
        session_config.read_dirs = _apply_decision_to_table(
            session_config.read_dirs, workspace_root, granted_paths, action)
    if touch_write:
        session_config.write_dirs = _apply_decision_to_table(
            session_config.write_dirs, workspace_root, granted_paths, action)

    if scope == "session":
        return

    if process_config is not None:
        if touch_read:
            process_config.session.read_dirs = _apply_decision_to_table(
                process_config.session.read_dirs, workspace_root, granted_paths, action)
        if touch_write:
            process_config.session.write_dirs = _apply_decision_to_table(
                process_config.session.write_dirs, workspace_root, granted_paths, action)

    if scope == "workspace":
        _apply_grant_to_file(
            project_config_path(workspace_root), workspace_root, granted_paths, is_write, action)
    else:
        _apply_grant_to_file(user_config_path(), workspace_root, granted_paths, is_write, action)
        _clean_ask_entries_only(project_config_path(workspace_root), workspace_root, granted_paths)
