# © Copyright 2026 Aaron Kimball
"""Computes and persists permission grants for `ActivateSkill`'s `(namespace, name)` asks -- the
`skillRules` counterpart to `klorb.permissions.command_grant`. Records what gets added to
`skillRules.allow`/`.deny` (and removed from `.ask`), in memory and on disk, when a user answers a
skill-activation ask with a persistent scope. See docs/specs/skills.md and docs/specs/permissions.md.
"""

from typing import Any

from klorb.permissions.grant import GrantAction, GrantScope
from klorb.permissions.rule_grant_base import RuleGrantWriter
from klorb.permissions.skill_access import SkillId, SkillRules, format_fqsn, parse_fqsn
from klorb.process_config import ProcessConfig, project_config_path, user_config_path
from klorb.session import SessionConfig

_SKILL_RULES_KEY = "skillRules"


def _fqsn_list(raw_category: Any) -> list[SkillId]:
    """Parse a config file's `skillRules` category (a list of `"<namespace>:<name>"` strings),
    skipping any malformed (non-string or separator-less) entry."""
    return [parse_fqsn(entry) for entry in raw_category if isinstance(entry, str) and ":" in entry]


def _skill_rules_from_json(raw: dict[str, Any]) -> SkillRules:
    return SkillRules(
        deny=_fqsn_list(raw.get("deny", [])),
        ask=_fqsn_list(raw.get("ask", [])),
        allow=_fqsn_list(raw.get("allow", [])),
    )


def _skill_rules_to_json(rules: SkillRules) -> dict[str, list[str]]:
    return {
        "deny": [format_fqsn(pair) for pair in rules.deny],
        "ask": [format_fqsn(pair) for pair in rules.ask],
        "allow": [format_fqsn(pair) for pair in rules.allow],
    }


_writer = RuleGrantWriter[SkillRules](
    config_key=_SKILL_RULES_KEY,
    make=lambda deny, ask, allow: SkillRules(deny=deny, ask=ask, allow=allow),
    from_json=_skill_rules_from_json, to_json=_skill_rules_to_json)
"""The `skillRules` instance of the generic single-table grant scaffolding shared with
`klorb.permissions.command_grant` -- see `klorb.permissions.rule_grant_base.RuleGrantWriter`."""


def apply_skill_permission_grant(
    action: GrantAction,
    scope: GrantScope,
    session_config: SessionConfig,
    process_config: ProcessConfig | None,
    skill_id: SkillId,
) -> None:
    """Record a permanent Allow or Deny decision for `skill_id` at `scope` -- the `skillRules`
    counterpart to `klorb.permissions.command_grant.apply_command_permission_grant`; see that
    function's docstring for the shared scope/cross-file semantics (`"session"` stops at the live
    `SessionConfig`; `"workspace"`/`"homedir"` additionally ripple into `process_config.session`
    and persist to the scope's target file; `"homedir"` additionally best-effort-cleans a redundant
    workspace-file `ask` entry, never the reverse).
    """
    granted = [skill_id]

    session_config.skill_rules = _writer.apply_decision(session_config.skill_rules, granted, action)

    if scope == "session":
        return

    if process_config is not None:
        process_config.session.skill_rules = _writer.apply_decision(
            process_config.session.skill_rules, granted, action)

    workspace_root = session_config.workspace.path.resolve()
    if scope == "workspace":
        _writer.apply_grant_to_file(project_config_path(workspace_root), granted, action)
    else:
        _writer.apply_grant_to_file(user_config_path(), granted, action)
        _writer.clean_ask_entries_only(project_config_path(workspace_root), granted)
