# © Copyright 2026 Aaron Kimball
"""Computes and persists permission grants for `BashTool`'s command-pattern asks — the
`commandRules` counterpart to `klorb.permissions.grant`'s directory grants. What gets added to
`commandRules.allow`/`.deny` (and removed from `.ask`), in memory and on disk, when a user
answers a `MultiPermissionAskRequired` item whose `command` (an argv pattern) is set rather than
its `path`. See docs/specs/bash-tool-and-command-permissions.md and
docs/specs/permissions.md's "Interactive ask confirmation" section, whose granularity and
cross-file rules this module mirrors for a different resource kind.

Writes directly to `klorb-config.json` files via `pathlib`/`json` (through
`klorb.schema_envelope`), exactly like `klorb.permissions.grant` — trusted harness code acting on
an explicit interactive user decision, not the agent, and not routed through any
`klorb.permissions` check itself.
"""

from pathlib import Path
from typing import Any

from klorb.permissions.command_access import CommandPermissionsTable, CommandRules
from klorb.permissions.grant import GrantAction, GrantScope
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

_COMMAND_RULES_KEY = "commandRules"


def compute_command_grant_patterns(command_rules: CommandRules, argv: list[str]) -> list[list[str]]:
    """The token pattern(s) a grant for `argv` should be recorded at: every `ask`-category rule
    that matches `argv`, or `[argv]` itself (an exact-match rule with no wildcards) if nothing
    matched at all. Unlike a directory grant's "the containing directory" fallback, there's no
    broader natural granularity for a command with no matching rule — recording the grant at the
    exact argv the model actually ran is the narrowest, least-surprising default; a user who
    wants a broader rule can always widen it by hand later.

    Pure and read-only — safe to call from both the TUI (to render the modal's copy) and inside
    `apply_command_permission_grant`.
    """
    table = CommandPermissionsTable(command_rules)
    matched = table.matching_rules("ask", argv)
    return matched if matched else [list(argv)]


def _apply_decision_to_table(
    rules: CommandRules, granted_patterns: list[list[str]], action: GrantAction,
) -> CommandRules:
    """Return a NEW `CommandRules`: `granted_patterns` appended to `action`'s own list (deduped
    against that list's existing entries), and any `ask` entry equal to one of `granted_patterns`
    removed. The *other* category is left untouched — same reasoning as
    `klorb.permissions.grant._apply_decision_to_table`. Never mutates `rules` in place, per
    `CommandRules`'s documented immutable-by-convention contract.
    """
    target = rules.allow if action == "allow" else rules.deny
    new_target = list(target)
    for pattern in granted_patterns:
        if pattern not in new_target:
            new_target.append(pattern)
    new_ask = [p for p in rules.ask if p not in granted_patterns]
    if action == "allow":
        return CommandRules(deny=list(rules.deny), ask=new_ask, allow=new_target)
    return CommandRules(deny=new_target, ask=new_ask, allow=list(rules.allow))


def _command_rules_from_json(raw: dict[str, Any]) -> CommandRules:
    return CommandRules(
        deny=[list(p) for p in raw.get("deny", [])],
        ask=[list(p) for p in raw.get("ask", [])],
        allow=[list(p) for p in raw.get("allow", [])],
    )


def _command_rules_to_json(rules: CommandRules) -> dict[str, list[list[str]]]:
    return {
        "deny": [list(p) for p in rules.deny],
        "ask": [list(p) for p in rules.ask],
        "allow": [list(p) for p in rules.allow],
    }


def _load_file_command_rules(path: Path) -> tuple[dict[str, Any], CommandRules]:
    raw = read_versioned_json(path, expected_schema_name=CONFIG_SCHEMA_NAME)
    session_defaults = raw.get(SESSION_DEFAULTS_KEY, {})
    return raw, _command_rules_from_json(session_defaults.get(_COMMAND_RULES_KEY, {}))


def _write_file_command_rules(path: Path, raw_contents: dict[str, Any], command_rules: CommandRules) -> None:
    session_defaults = dict(raw_contents.get(SESSION_DEFAULTS_KEY, {}))
    session_defaults[_COMMAND_RULES_KEY] = _command_rules_to_json(command_rules)
    new_contents = dict(raw_contents)
    new_contents[SESSION_DEFAULTS_KEY] = session_defaults
    write_versioned_json(
        path, new_contents, schema_name=CONFIG_SCHEMA_NAME, schema_version=CONFIG_SCHEMA_VERSION)


def _apply_grant_to_file(path: Path, granted_patterns: list[list[str]], action: GrantAction) -> None:
    raw, file_command_rules = _load_file_command_rules(path)
    new_command_rules = _apply_decision_to_table(file_command_rules, granted_patterns, action)
    _write_file_command_rules(path, raw, new_command_rules)


def _clean_ask_entries_only(path: Path, granted_patterns: list[list[str]]) -> None:
    """Best-effort: if `path` exists and its own `commandRules.ask` contains a pattern in
    `granted_patterns`, remove it and write the file back — WITHOUT adding anything to either
    `allow`/`deny` list. A no-op if `path` doesn't exist or nothing matches. Mirrors
    `klorb.permissions.grant._clean_ask_entries_only`'s homedir-may-clean-workspace asymmetry.
    """
    if not path.is_file():
        return
    raw, file_command_rules = _load_file_command_rules(path)
    new_ask = [p for p in file_command_rules.ask if p not in granted_patterns]
    if new_ask == file_command_rules.ask:
        return
    new_command_rules = CommandRules(
        deny=file_command_rules.deny, ask=new_ask, allow=file_command_rules.allow)
    _write_file_command_rules(path, raw, new_command_rules)


def apply_command_permission_grant(
    action: GrantAction,
    scope: GrantScope,
    session_config: SessionConfig,
    process_config: ProcessConfig | None,
    argv: list[str],
) -> None:
    """Record a permanent Allow or Deny decision for `argv` at `scope` — the `commandRules`
    counterpart to `klorb.permissions.grant.apply_permission_grant`; see that function's
    docstring for the shared scope/cross-file semantics (`"session"` stops at the live
    `SessionConfig`; `"workspace"`/`"homedir"` additionally ripple into `process_config.session`
    and persist to the scope's target file; `"homedir"` additionally best-effort-cleans a
    redundant workspace-file `ask` entry, never the reverse).
    """
    granted_patterns = compute_command_grant_patterns(session_config.command_rules, argv)

    session_config.command_rules = _apply_decision_to_table(
        session_config.command_rules, granted_patterns, action)

    if scope == "session":
        return

    if process_config is not None:
        process_config.session.command_rules = _apply_decision_to_table(
            process_config.session.command_rules, granted_patterns, action)

    workspace_root = session_config.workspace.path.resolve()
    if scope == "workspace":
        _apply_grant_to_file(project_config_path(workspace_root), granted_patterns, action)
    else:
        _apply_grant_to_file(user_config_path(), granted_patterns, action)
        _clean_ask_entries_only(project_config_path(workspace_root), granted_patterns)
