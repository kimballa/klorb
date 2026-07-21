# © Copyright 2026 Aaron Kimball
"""Computes and persists permission grants for `WebFetch`'s domain asks — the `domains`
counterpart to `klorb.permissions.skill_grant`. Records what gets added to
`domains.allow`/`.deny` (and removed from `.ask`), in memory and on disk, when a user
answers a domain-access ask with a persistent scope. See docs/specs/web-fetch-tool.md and
docs/specs/permissions.md.
"""

from typing import Any

from klorb.permissions.domain_access import DomainRules
from klorb.permissions.grant import GrantAction, GrantScope
from klorb.permissions.rule_grant_base import RuleGrantWriter
from klorb.process_config import ProcessConfig, project_config_path, user_config_path
from klorb.session import SessionConfig

_DOMAINS_KEY = "domains"


def _domain_list(raw_category: Any) -> list[str]:
    """Parse a config file's `domains` category (a list of domain strings), skipping any
    malformed (non-string) entry."""
    return [entry for entry in raw_category if isinstance(entry, str)]


def _domain_rules_from_json(raw: dict[str, Any]) -> DomainRules:
    return DomainRules(
        deny=_domain_list(raw.get("deny", [])),
        ask=_domain_list(raw.get("ask", [])),
        allow=_domain_list(raw.get("allow", [])),
    )


def _domain_rules_to_json(rules: DomainRules) -> dict[str, list[str]]:
    return {
        "deny": list(rules.deny),
        "ask": list(rules.ask),
        "allow": list(rules.allow),
    }


_writer = RuleGrantWriter[DomainRules](
    config_key=_DOMAINS_KEY,
    make=lambda deny, ask, allow: DomainRules(deny=deny, ask=ask, allow=allow),
    from_json=_domain_rules_from_json, to_json=_domain_rules_to_json)
"""The `domains` instance of the generic single-table grant scaffolding shared with
`klorb.permissions.command_grant`/`klorb.permissions.skill_grant` — see
`klorb.permissions.rule_grant_base.RuleGrantWriter`."""


def apply_domain_permission_grant(
    action: GrantAction,
    scope: GrantScope,
    session_config: SessionConfig,
    process_config: ProcessConfig | None,
    domain: str,
) -> None:
    """Record a permanent Allow or Deny decision for `domain` at `scope` — the `domains`
    counterpart to `klorb.permissions.skill_grant.apply_skill_permission_grant`; see that
    function's docstring for the shared scope/cross-file semantics (`"session"` stops at the
    live `SessionConfig`; `"workspace"`/`"homedir"` additionally ripple into
    `process_config.session` and persist to the scope's target file; `"homedir"` additionally
    best-effort-cleans a redundant workspace-file `ask` entry, never the reverse).
    """
    granted = [domain]

    session_config.domain_rules = _writer.apply_decision(
        session_config.domain_rules, granted, action)

    if scope == "session":
        return

    if process_config is not None:
        process_config.session.domain_rules = _writer.apply_decision(
            process_config.session.domain_rules, granted, action)

    workspace_root = session_config.workspace.path.resolve()
    if scope == "workspace":
        _writer.apply_grant_to_file(project_config_path(workspace_root), granted, action)
    else:
        _writer.apply_grant_to_file(user_config_path(), granted, action)
        _writer.clean_ask_entries_only(project_config_path(workspace_root), granted)
