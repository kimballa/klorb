# © Copyright 2026 Aaron Kimball
"""Tests for klorb.permissions.command_access: CommandRules/CommandPermissionsTable token-
pattern matching and deny/ask/allow precedence. See
docs/plans/ready/004-bash-permissions-and-bash-tool.md's "CommandRules parsing / matching with
shfmt" section.
"""

from klorb.permissions.command_access import CommandPermissionsTable, CommandRules


def _table(**kwargs: list[list[str]]) -> CommandPermissionsTable:
    return CommandPermissionsTable(CommandRules(**kwargs))


# --- token pattern matching shapes ---


def test_bare_argv0_with_no_args_matches_only_exact_length() -> None:
    """argv0, forcibly no args (`foo`) -- deliberately different from `foo *`."""
    table = _table(allow=[["foo"]])
    assert table.evaluate(["foo"]) == "allow"
    assert table.evaluate(["foo", "bar"]) is None


def test_trailing_wildcard_matches_any_number_of_further_args_including_zero() -> None:
    table = _table(allow=[["foo", "*"]])
    assert table.evaluate(["foo"]) == "allow"
    assert table.evaluate(["foo", "bar"]) == "allow"
    assert table.evaluate(["foo", "bar", "baz"]) == "allow"


def test_argv0_with_named_subcommand_and_any_args() -> None:
    table = _table(allow=[["git", "status", "*"]])
    assert table.evaluate(["git", "status"]) == "allow"
    assert table.evaluate(["git", "status", "-s"]) == "allow"
    assert table.evaluate(["git", "commit"]) is None


def test_mid_pattern_wildcard_matches_exactly_one_token() -> None:
    """`git * status *` -- the middle `*` is not optional: it consumes exactly one token."""
    table = _table(allow=[["git", "*", "status", "*"]])
    assert table.evaluate(["git", "foo", "status", "bar", "baz"]) == "allow"
    assert table.evaluate(["git", "status"]) is None  # nothing between git and status


def test_exact_literal_args_require_exact_match() -> None:
    table = _table(allow=[["foo", "--bar", "--baz"]])
    assert table.evaluate(["foo", "--bar", "--baz"]) == "allow"
    assert table.evaluate(["foo", "--bar"]) is None
    assert table.evaluate(["foo", "--bar", "--baz", "extra"]) is None


def test_no_matching_rule_returns_none() -> None:
    table = _table(allow=[["git", "*"]])
    assert table.evaluate(["ls", "-la"]) is None


# --- deny/ask/allow category precedence ---


def test_deny_beats_allow_regardless_of_specificity() -> None:
    table = _table(deny=[["git", "push", "*"]], allow=[["git", "*"]])
    assert table.evaluate(["git", "status"]) == "allow"
    assert table.evaluate(["git", "push", "origin", "main"]) == "deny"


def test_ask_wins_over_allow_but_loses_to_deny() -> None:
    all_three = _table(deny=[["rm", "*"]], ask=[["rm", "*"]], allow=[["rm", "*"]])
    assert all_three.evaluate(["rm", "-rf", "/"]) == "deny"

    ask_and_allow = _table(ask=[["rm", "*"]], allow=[["rm", "*"]])
    assert ask_and_allow.evaluate(["rm", "-rf", "/"]) == "ask"


def test_matching_rules_reports_every_matching_rule_in_a_category() -> None:
    table = _table(ask=[["git", "*"], ["git", "push", "*"]])
    matches = table.matching_rules("ask", ["git", "push", "origin"])
    assert matches == [["git", "*"], ["git", "push", "*"]]
