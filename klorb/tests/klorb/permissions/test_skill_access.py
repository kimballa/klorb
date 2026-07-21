# © Copyright 2026 Aaron Kimball
"""Tests for klorb.permissions.skill_access."""

import pytest

from klorb.permissions.skill_access import (
    VALID_NAMESPACES,
    SkillRules,
    SkillsAccessTable,
    evaluate_skill,
    format_fqsn,
    normalize_skill_verdict,
    parse_fqsn,
)


def test_valid_namespaces() -> None:
    # Precedence order: user (homedir), then workspace (project), then internal (packaged).
    assert VALID_NAMESPACES == ("user", "workspace", "internal")


def test_unmatched_pair_evaluates_to_none_normalized_to_ask() -> None:
    table = SkillsAccessTable(SkillRules())
    assert table.evaluate(("internal", "foo")) is None
    assert normalize_skill_verdict(None) == "ask"
    assert evaluate_skill(SkillRules(), ("internal", "foo")) == "ask"


def test_exact_tuple_match_only() -> None:
    rules = SkillRules(allow=[("internal", "foo")])
    assert evaluate_skill(rules, ("internal", "foo")) == "allow"
    # Same name, different namespace does not match.
    assert evaluate_skill(rules, ("workspace", "foo")) == "ask"
    # Same namespace, different name does not match.
    assert evaluate_skill(rules, ("internal", "bar")) == "ask"


def test_deny_beats_allow_regardless_of_order() -> None:
    rules = SkillRules(deny=[("workspace", "foo")], allow=[("workspace", "foo")])
    assert evaluate_skill(rules, ("workspace", "foo")) == "deny"


def test_ask_category() -> None:
    rules = SkillRules(ask=[("user", "foo")])
    assert evaluate_skill(rules, ("user", "foo")) == "ask"


def test_fqsn_round_trips() -> None:
    assert format_fqsn(("internal", "create-edit-skill")) == "internal:create-edit-skill"
    assert parse_fqsn("workspace:add-cli-flag") == ("workspace", "add-cli-flag")
    assert parse_fqsn(format_fqsn(("user", "foo"))) == ("user", "foo")


def test_parse_fqsn_requires_separator() -> None:
    with pytest.raises(ValueError, match="namespace"):
        parse_fqsn("no-separator")
