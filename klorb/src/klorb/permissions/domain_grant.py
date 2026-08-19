# © Copyright 2026 Aaron Kimball
"""Computes and persists permission grants for a domain-access ask."""

from typing import Any

from klorb.permissions.domain_access import DomainRules
from klorb.permissions.grant import GrantAction, GrantScope
from klorb.permissions.rule_grant_base import RuleGrantWriter
from klorb.process_config import ProcessConfig, project_config_path, user_config_path
from klorb.session import SessionConfig

_WEB_DOMAINS_KEY = "webDomains"
_BASH_DOMAINS_KEY = "bashDomains"


def _domain_list(raw_category: Any) -> list[str]:
    """Parse a config file's `webDomains`/`bashDomains` category (a list of domain strings),
    skipping any malformed (non-string) entry."""
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


_web_writer = RuleGrantWriter[DomainRules](
    config_key=_WEB_DOMAINS_KEY,
    make=lambda deny, ask, allow: DomainRules(deny=deny, ask=ask, allow=allow),
    from_json=_domain_rules_from_json, to_json=_domain_rules_to_json)
"""The `webDomains` instance of the generic single-table grant scaffolding."""

_bash_writer = RuleGrantWriter[DomainRules](
    config_key=_BASH_DOMAINS_KEY,
    make=lambda deny, ask, allow: DomainRules(deny=deny, ask=ask, allow=allow),
    from_json=_domain_rules_from_json, to_json=_domain_rules_to_json)
"""The `bashDomains` instance."""


def apply_domain_permission_grant(
    action: GrantAction,
    scope: GrantScope,
    session_config: SessionConfig,
    process_config: ProcessConfig | None,
    domain: str,
    *, rule_set: str = "web",
) -> None:
    """Record a permanent Allow or Deny decision for `domain` at `scope`, against the
    `webDomains` table or the `bashDomains` table.
    """
    granted = [domain]
    writer = _bash_writer if rule_set == "bash" else _web_writer

    if rule_set == "bash":
        session_config.bash_domain_rules = writer.apply_decision(
            session_config.bash_domain_rules, granted, action)
    else:
        session_config.web_domain_rules = writer.apply_decision(
            session_config.web_domain_rules, granted, action)

    if scope == "session":
        return

    if process_config is not None:
        if rule_set == "bash":
            process_config.session.bash_domain_rules = writer.apply_decision(
                process_config.session.bash_domain_rules, granted, action)
        else:
            process_config.session.web_domain_rules = writer.apply_decision(
                process_config.session.web_domain_rules, granted, action)

    workspace_root = session_config.workspace.path.resolve()
    if scope == "workspace":
        writer.apply_grant_to_file(project_config_path(workspace_root), granted, action)
    else:
        writer.apply_grant_to_file(user_config_path(), granted, action)
        writer.clean_ask_entries_only(project_config_path(workspace_root), granted)
