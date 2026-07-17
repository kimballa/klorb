# © Copyright 2026 Aaron Kimball
"""Computes and persists permission grants for `ActivateSkill`'s `(namespace, name)` asks -- the
`skillRules` counterpart to `klorb.permissions.command_grant`. Records what gets added to
`skillRules.allow`/`.deny` (and removed from `.ask`), in memory and on disk, when a user answers a
skill-activation ask with a persistent scope. See docs/specs/skills.md and docs/specs/permissions.md.
"""

from pathlib import Path
from typing import Any

from klorb.permissions.grant import GrantAction, GrantScope
from klorb.permissions.skill_access import SkillId, SkillRules, format_fqsn, parse_fqsn
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

_SKILL_RULES_KEY = "skillRules"


def _apply_decision_to_table(
    rules: SkillRules, skill_id: SkillId, action: GrantAction,
) -> SkillRules:
    """Return a NEW `SkillRules`: `skill_id` appended to `action`'s list (deduped), and any `ask`
    entry equal to `skill_id` removed. The other category is left untouched. Never mutates `rules`
    in place."""
    target = rules.allow if action == "allow" else rules.deny
    new_target = list(target)
    if skill_id not in new_target:
        new_target.append(skill_id)
    new_ask = [pair for pair in rules.ask if pair != skill_id]
    if action == "allow":
        return SkillRules(deny=list(rules.deny), ask=new_ask, allow=new_target)
    return SkillRules(deny=new_target, ask=new_ask, allow=list(rules.allow))


def _fqsn_list(raw_category: Any) -> list[SkillId]:
    """Parse a config file's `skillRules` category (a list of `"<namespace>/<name>"` strings),
    skipping any malformed (non-string or separator-less) entry."""
    return [parse_fqsn(entry) for entry in raw_category if isinstance(entry, str) and "/" in entry]


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


def _load_file_skill_rules(path: Path) -> tuple[dict[str, Any], SkillRules]:
    raw = read_versioned_json(path, expected_schema_name=CONFIG_SCHEMA_NAME)
    session_defaults = raw.get(SESSION_DEFAULTS_KEY, {})
    return raw, _skill_rules_from_json(session_defaults.get(_SKILL_RULES_KEY, {}))


def _write_file_skill_rules(path: Path, raw_contents: dict[str, Any], skill_rules: SkillRules) -> None:
    session_defaults = dict(raw_contents.get(SESSION_DEFAULTS_KEY, {}))
    session_defaults[_SKILL_RULES_KEY] = _skill_rules_to_json(skill_rules)
    new_contents = dict(raw_contents)
    new_contents[SESSION_DEFAULTS_KEY] = session_defaults
    write_versioned_json(
        path, new_contents, schema_name=CONFIG_SCHEMA_NAME, schema_version=CONFIG_SCHEMA_VERSION)


def _apply_grant_to_file(path: Path, skill_id: SkillId, action: GrantAction) -> None:
    raw, file_skill_rules = _load_file_skill_rules(path)
    new_skill_rules = _apply_decision_to_table(file_skill_rules, skill_id, action)
    _write_file_skill_rules(path, raw, new_skill_rules)


def _clean_ask_entries_only(path: Path, skill_id: SkillId) -> None:
    """Best-effort: if `path` exists and its `skillRules.ask` contains `skill_id`, remove it and
    write the file back, adding nothing to `allow`/`deny`. A no-op if `path` doesn't exist or
    nothing matches."""
    if not path.is_file():
        return
    raw, file_skill_rules = _load_file_skill_rules(path)
    new_ask = [pair for pair in file_skill_rules.ask if pair != skill_id]
    if new_ask == file_skill_rules.ask:
        return
    new_skill_rules = SkillRules(
        deny=file_skill_rules.deny, ask=new_ask, allow=file_skill_rules.allow)
    _write_file_skill_rules(path, raw, new_skill_rules)


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
    session_config.skill_rules = _apply_decision_to_table(
        session_config.skill_rules, skill_id, action)

    if scope == "session":
        return

    if process_config is not None:
        process_config.session.skill_rules = _apply_decision_to_table(
            process_config.session.skill_rules, skill_id, action)

    workspace_root = session_config.workspace.path.resolve()
    if scope == "workspace":
        _apply_grant_to_file(project_config_path(workspace_root), skill_id, action)
    else:
        _apply_grant_to_file(user_config_path(), skill_id, action)
        _clean_ask_entries_only(project_config_path(workspace_root), skill_id)
