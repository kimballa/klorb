# © Copyright 2026 Aaron Kimball
"""Tests for klorb.permissions.skill_access."""

from klorb.permissions.skill_access import (
    VALID_NAMESPACES,
    SkillRules,
    SkillsAccessTable,
    evaluate_skill,
    normalize_skill_verdict,
)


def test_valid_namespaces() -> None:
    assert VALID_NAMESPACES == ("workspace", "user", "internal")


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


def test_rules_coerce_lists_to_tuples() -> None:
    # On-disk shape is a two-element list; pydantic coerces to a tuple.
    rules = SkillRules.model_validate({"allow": [["internal", "foo"]]})
    assert rules.allow == [("internal", "foo")]
    assert evaluate_skill(rules, ("internal", "foo")) == "allow"
