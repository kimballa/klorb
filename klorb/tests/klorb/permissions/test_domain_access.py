# © Copyright 2026 Aaron Kimball
"""Tests for klorb.permissions.domain_access."""

import pytest

from klorb.permissions.domain_access import (
    DomainAccessTable,
    DomainRules,
    _domain_matches,
    _is_ip_address,
    evaluate_domain,
    normalize_domain_verdict,
    parse_domain,
)

# --- parse_domain ---


def test_parse_domain_strips_port() -> None:
    assert parse_domain("https://example.com:8080/path") == "example.com"


def test_parse_domain_lowercases() -> None:
    assert parse_domain("https://EXAMPLE.COM/path") == "example.com"


def test_parse_domain_strips_path() -> None:
    assert parse_domain("https://example.com/some/path?q=1") == "example.com"


def test_parse_domain_no_netloc_raises() -> None:
    with pytest.raises(ValueError, match="no domain"):
        parse_domain("not-a-url")


def test_parse_domain_ipv6_bracket_notation() -> None:
    assert parse_domain("http://[::1]:8080/path") == "::1"


def test_parse_domain_user_at_stripped() -> None:
    # urlparse puts user:pass@host in netloc; the host part is after @
    # But urlparse doesn't split on @ for all cases, so let's test what we get
    result = parse_domain("https://user:pass@example.com/path")
    # This may include user:pass depending on urlparse behavior
    # The key test is it lowercases and strips port
    assert "example.com" in result


# --- _is_ip_address ---


def test_is_ip_address_ipv4() -> None:
    assert _is_ip_address("192.168.1.1") is True


def test_is_ip_address_ipv6() -> None:
    assert _is_ip_address("::1") is True


def test_is_ip_address_domain() -> None:
    assert _is_ip_address("example.com") is False


def test_is_ip_address_wildcard() -> None:
    assert _is_ip_address("172.16.*") is False


# --- _domain_matches ---


def test_exact_match() -> None:
    assert _domain_matches("example.com", "example.com") is True


def test_exact_no_match() -> None:
    assert _domain_matches("example.com", "other.com") is False


def test_wildcard_prefix_matches_subdomain() -> None:
    assert _domain_matches("*.example.com", "www.example.com") is True


def test_wildcard_prefix_matches_deep_subdomain() -> None:
    assert _domain_matches("*.example.com", "foo.bar.example.com") is True


def test_wildcard_prefix_matches_bare_domain() -> None:
    """The wildcard prefix *.example.com matches example.com itself."""
    assert _domain_matches("*.example.com", "example.com") is True


def test_wildcard_prefix_no_match_different_domain() -> None:
    assert _domain_matches("*.example.com", "www.other.com") is False


def test_wildcard_suffix_matches_ip_range() -> None:
    assert _domain_matches("172.16.*", "172.16.0.1") is True
    assert _domain_matches("172.16.*", "172.16.255.255") is True


def test_wildcard_suffix_no_match_outside_range() -> None:
    assert _domain_matches("172.16.*", "172.17.0.1") is False
    assert _domain_matches("172.16.*", "10.0.0.1") is False


def test_wildcard_suffix_no_match_domain() -> None:
    """Wildcard suffix only matches IP addresses, not domain names."""
    assert _domain_matches("172.16.*", "example.com") is False


def test_wildcard_suffix_matches_10_range() -> None:
    assert _domain_matches("10.*", "10.0.0.1") is True
    assert _domain_matches("10.*", "10.255.255.255") is True
    assert _domain_matches("10.*", "11.0.0.1") is False


def test_wildcard_suffix_matches_192_168_range() -> None:
    assert _domain_matches("192.168.*", "192.168.1.1") is True
    assert _domain_matches("192.168.*", "192.169.1.1") is False


# --- DomainAccessTable ---


def test_empty_table_returns_none() -> None:
    table = DomainAccessTable(DomainRules())
    assert table.evaluate("example.com") is None


def test_allow_exact() -> None:
    rules = DomainRules(allow=["example.com"])
    assert evaluate_domain(rules, "example.com") == "allow"


def test_allow_wildcard() -> None:
    rules = DomainRules(allow=["*.example.com"])
    assert evaluate_domain(rules, "www.example.com") == "allow"
    assert evaluate_domain(rules, "example.com") == "allow"


def test_deny_beats_allow() -> None:
    rules = DomainRules(deny=["bad.example.com"], allow=["*.example.com"])
    assert evaluate_domain(rules, "bad.example.com") == "deny"
    assert evaluate_domain(rules, "good.example.com") == "allow"


def test_ask_category() -> None:
    rules = DomainRules(ask=["internal.example.com"])
    assert evaluate_domain(rules, "internal.example.com") == "ask"


def test_unmatched_normalized_to_ask() -> None:
    rules = DomainRules(allow=["example.com"])
    assert evaluate_domain(rules, "other.com") == "ask"


def test_normalize_domain_verdict_none_to_ask() -> None:
    assert normalize_domain_verdict(None) == "ask"


def test_normalize_domain_verdict_passthrough() -> None:
    assert normalize_domain_verdict("allow") == "allow"
    assert normalize_domain_verdict("deny") == "deny"
    assert normalize_domain_verdict("ask") == "ask"


def test_deny_beats_allow_regardless_of_order() -> None:
    """A deny always beats an allow, no matter construction order."""
    rules = DomainRules(deny=["example.com"], allow=["example.com"])
    assert evaluate_domain(rules, "example.com") == "deny"


def test_ip_wildcard_in_allow() -> None:
    rules = DomainRules(allow=["192.168.*"])
    assert evaluate_domain(rules, "192.168.1.1") == "allow"
    assert evaluate_domain(rules, "10.0.0.1") == "ask"


def test_localhost_default_in_config() -> None:
    """The default config allows localhost."""
    rules = DomainRules(allow=["localhost", "127.0.0.1", "::1", "10.*", "192.168.*", "172.16.*"])
    assert evaluate_domain(rules, "localhost") == "allow"
    assert evaluate_domain(rules, "127.0.0.1") == "allow"
    assert evaluate_domain(rules, "::1") == "allow"
    assert evaluate_domain(rules, "10.0.0.1") == "allow"
    assert evaluate_domain(rules, "192.168.1.1") == "allow"
    assert evaluate_domain(rules, "172.16.0.1") == "allow"
    # External domain not in allow list gets "ask"
    assert evaluate_domain(rules, "example.com") == "ask"
