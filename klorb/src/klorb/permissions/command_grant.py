# © Copyright 2026 Aaron Kimball
"""Computes and persists permission grants for `BashTool`'s command-pattern asks."""

from typing import Any

from klorb.permissions.command_access import CommandPermissionsTable, CommandRules
from klorb.permissions.grant import GrantAction, GrantScope
from klorb.permissions.rule_grant_base import RuleGrantWriter
from klorb.process_config import ProcessConfig, project_config_path, user_config_path
from klorb.session import SessionConfig

_COMMAND_RULES_KEY = "commandRules"


def compute_command_grant_patterns(command_rules: CommandRules, argv: list[str]) -> list[list[str]]:
    """The token pattern(s) a grant for `argv` should be recorded at: every `ask`-category rule
    that matches `argv`, or `[argv]` itself (an exact-match rule with no wildcards) if nothing
    matched at all.

    Pure and read-only.
    """
    table = CommandPermissionsTable(command_rules)
    matched = table.matching_rules("ask", argv)
    return matched if matched else [list(argv)]


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


_writer = RuleGrantWriter[CommandRules](
    config_key=_COMMAND_RULES_KEY,
    make=lambda deny, ask, allow: CommandRules(deny=deny, ask=ask, allow=allow),
    from_json=_command_rules_from_json, to_json=_command_rules_to_json)
"""The `commandRules` instance of the generic single-table grant scaffolding."""


def apply_command_permission_grant(
    action: GrantAction,
    scope: GrantScope,
    session_config: SessionConfig,
    process_config: ProcessConfig | None,
    argv: list[str],
    patterns: list[list[str]] | None = None,
) -> None:
    """Record a permanent Allow or Deny decision for `argv` at `scope`.

    `patterns`, when given, is recorded as-is instead of being recomputed from `argv`.
    `None` (the default) recomputes from `argv`, for callers with no precomputed patterns of their own.
    """
    granted_patterns = patterns if patterns is not None else compute_command_grant_patterns(
        session_config.command_rules, argv)

    session_config.command_rules = _writer.apply_decision(
        session_config.command_rules, granted_patterns, action)

    if scope == "session":
        return

    if process_config is not None:
        process_config.session.command_rules = _writer.apply_decision(
            process_config.session.command_rules, granted_patterns, action)

    workspace_root = session_config.workspace.path.resolve()
    if scope == "workspace":
        _writer.apply_grant_to_file(project_config_path(workspace_root), granted_patterns, action)
    else:
        _writer.apply_grant_to_file(user_config_path(), granted_patterns, action)
        _writer.clean_ask_entries_only(project_config_path(workspace_root), granted_patterns)
