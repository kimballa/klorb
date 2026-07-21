# © Copyright 2026 Aaron Kimball
"""Domain access control: a `PermissionsTable` resource kind governing which domains the
`WebFetch` tool may retrieve content from, keyed on the domain string (lowercased, port
stripped). See docs/specs/web-fetch-tool.md and docs/specs/permissions.md.
"""

import ipaddress
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from klorb.permissions.table import PermissionsTable, Verdict

DomainSpec = str
"""A domain string like ``"example.com"`` or ``"*.example.com"``, or an IP address like
``"192.168.1.1"`` or ``"172.16.*"``."""


class DomainRules(BaseModel):
    """One ``domains`` config key's ``deny``/``ask``/``allow`` rule lists of domain strings.
    On disk each entry is a plain domain string (e.g. ``"example.com"``, ``"*.example.com"``),
    not a nested array. Immutable after construction, like ``SkillRules``.
    """

    deny: list[DomainSpec] = Field(default_factory=list)
    ask: list[DomainSpec] = Field(default_factory=list)
    allow: list[DomainSpec] = Field(default_factory=list)


def parse_domain(url: str) -> str:
    """Extract the domain from a URL: the ``netloc`` component, lowercased, with port and
    user info stripped. Raises ``ValueError`` if the URL cannot be parsed or has no netloc.
    """
    parsed = urlparse(url)
    netloc = parsed.netloc
    if not netloc:
        raise ValueError(f"URL has no domain: {url!r}")
    if not parsed.hostname:
        raise ValueError(f"URL has no hostname: {url!r}")
    return parsed.hostname


def _is_ip_address(domain: str) -> bool:
    """Return whether ``domain`` is a valid IPv4 or IPv6 address (not a wildcard pattern)."""
    try:
        ipaddress.ip_address(domain)
        return True
    except ValueError:
        return False


def _domain_matches(rule: str, candidate: str) -> bool:
    """Return whether ``rule`` matches ``candidate``.

    Matching semantics:

    1. Exact literal match: ``rule == candidate``.
    2. Wildcard prefix ``*.example.com``: matches ``x.example.com``,
       ``foo.bar.example.com``, and ``example.com`` itself (the bare domain).
    3. Wildcard suffix ``172.16.*``: matches IP addresses in that range
       (``172.16.0.1``, ``172.16.255.255``, etc.). Only for IP addresses.
    """
    # Exact match
    if rule == candidate:
        return True

    # Wildcard prefix for domains: *.example.com
    if rule.startswith("*."):
        base_domain = rule[2:]  # strip "*."
        # Matches the bare domain itself or any subdomain
        if candidate == base_domain:
            return True
        if candidate.endswith("." + base_domain):
            return True
        return False

    # Wildcard suffix for IP addresses: 172.16.*
    if rule.endswith(".*") and _is_ip_address(candidate):
        prefix = rule[:-2]  # strip ".*"
        try:
            # Pad partial prefix to full IP (e.g. "172.16" -> "172.16.0.0")
            octets = prefix.split(".")
            while len(octets) < 4:
                octets.append("0")
            full_prefix = ".".join(octets)
            prefix_addr = ipaddress.ip_address(full_prefix)
            candidate_addr = ipaddress.ip_address(candidate)
            # Mask based on number of original octets in the prefix
            mask_bits = len(prefix.split(".")) * 8
            total_bits = 32 if isinstance(prefix_addr, ipaddress.IPv4Address) else 128
            mask = (1 << total_bits) - (1 << (total_bits - mask_bits))
            return (int(prefix_addr) & mask) == (int(candidate_addr) & mask)
        except ValueError:
            return False

    return False


class DomainAccessTable(PermissionsTable[DomainSpec]):
    """A `PermissionsTable` over domain strings, matched by exact equality, wildcard prefix
    (``*.example.com``), or wildcard suffix (``172.16.*`` for IP addresses). A candidate
    matching no rule evaluates to ``None``, which `normalize_domain_verdict` folds to
    ``"ask"``.
    """

    def __init__(self, rules: DomainRules) -> None:
        super().__init__(
            deny=list(rules.deny), ask=list(rules.ask), allow=list(rules.allow))

    def _matches(self, rule: DomainSpec, candidate: DomainSpec) -> bool:
        return _domain_matches(rule, candidate)


def normalize_domain_verdict(verdict: Verdict | None) -> Verdict:
    """Fold ``DomainAccessTable.evaluate()``'s ``None`` (no matching rule) to ``"ask"``, so a
    domain never gets fetched merely because nothing denied it.
    """
    return verdict if verdict is not None else "ask"


def evaluate_domain(domain_rules: DomainRules, domain: str) -> Verdict:
    """Return the normalized ``deny``/``ask``/``allow`` verdict for ``domain`` against
    ``domain_rules``.
    """
    return normalize_domain_verdict(DomainAccessTable(domain_rules).evaluate(domain))
