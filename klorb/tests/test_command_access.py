# © Copyright 2026 Aaron Kimball
"""Tests for klorb.permissions.command_access: CommandRules/CommandPermissionsTable token-
pattern matching and deny/ask/allow precedence. See
docs/specs/bash-tool-and-command-permissions.md and
docs/adrs/command-rule-wildcards-double-star-unbounded-anywhere-question-mark-always-optional.md.
"""

from klorb.permissions.command_access import CommandPermissionsTable, CommandRules


def _table(**kwargs: list[list[str]]) -> CommandPermissionsTable:
    return CommandPermissionsTable(CommandRules(**kwargs))


# --- token pattern matching shapes ---


def test_bare_argv0_with_no_args_matches_only_exact_length() -> None:
    """argv0, forcibly no args (`foo`) -- deliberately different from `foo ?`."""
    table = _table(allow=[["foo"]])
    assert table.evaluate(["foo"]) == "allow"
    assert table.evaluate(["foo", "bar"]) is None


def test_star_matches_exactly_one_token_even_in_trailing_position() -> None:
    """`*` always means exactly one token, regardless of position -- it is never "any number,"
    including as a rule's last token."""
    table = _table(allow=[["foo", "*"]])
    assert table.evaluate(["foo"]) is None  # zero extra tokens: not a match
    assert table.evaluate(["foo", "bar"]) == "allow"  # exactly one extra token: a match
    assert table.evaluate(["foo", "bar", "baz"]) is None  # two extra tokens: not a match


def test_trailing_question_mark_matches_zero_or_one_token() -> None:
    """`?` means zero-or-one uniformly, regardless of position -- including as a rule's last
    token, it is never "any number." """
    table = _table(allow=[["foo", "?"]])
    assert table.evaluate(["foo"]) == "allow"
    assert table.evaluate(["foo", "bar"]) == "allow"
    assert table.evaluate(["foo", "bar", "baz"]) is None


def test_non_trailing_question_mark_matches_zero_or_one_token() -> None:
    """A `?` that isn't the rule's last token needs backtracking to resolve: whether it consumes
    a token depends on what the rest of the pattern needs to match next."""
    table = _table(allow=[["git", "?", "status"]])
    assert table.evaluate(["git", "status"]) == "allow"  # ? consumes zero tokens
    assert table.evaluate(["git", "--no-pager", "status"]) == "allow"  # ? consumes one token
    assert table.evaluate(["git", "a", "b", "status"]) is None  # two tokens is too many for ?
    assert table.evaluate(["git", "commit"]) is None


def test_trailing_double_star_matches_any_number_of_further_args_including_zero() -> None:
    table = _table(allow=[["foo", "**"]])
    assert table.evaluate(["foo"]) == "allow"
    assert table.evaluate(["foo", "bar"]) == "allow"
    assert table.evaluate(["foo", "bar", "baz"]) == "allow"


def test_mid_pattern_double_star_matches_any_number_of_tokens_including_zero() -> None:
    """`git ** status` -- the `**` needs backtracking to resolve, since how many tokens it
    consumes depends on where `status` actually falls in the candidate."""
    table = _table(allow=[["git", "**", "status"]])
    assert table.evaluate(["git", "status"]) == "allow"  # ** consumes zero tokens
    assert table.evaluate(["git", "-C", "dir", "status"]) == "allow"  # ** consumes two tokens
    assert table.evaluate(["git", "status", "extra"]) is None  # nothing consumes "extra"


def test_double_star_on_both_sides_of_a_literal() -> None:
    table = _table(allow=[["git", "**", "status", "**"]])
    assert table.evaluate(["git", "status"]) == "allow"
    assert table.evaluate(["git", "-C", "dir", "status", "-s", "-b"]) == "allow"
    assert table.evaluate(["git", "commit"]) is None


def test_argv0_with_named_subcommand_and_any_args() -> None:
    table = _table(allow=[["git", "status", "**"]])
    assert table.evaluate(["git", "status"]) == "allow"
    assert table.evaluate(["git", "status", "-s"]) == "allow"
    assert table.evaluate(["git", "status", "-s", "-b"]) == "allow"
    assert table.evaluate(["git", "commit"]) is None


def test_mid_pattern_star_matches_exactly_one_token() -> None:
    """`git * status **` -- the middle `*` is not optional: it consumes exactly one token; the
    trailing `**` then covers any number of further args."""
    table = _table(allow=[["git", "*", "status", "**"]])
    assert table.evaluate(["git", "foo", "status", "bar", "baz"]) == "allow"
    assert table.evaluate(["git", "status"]) is None  # nothing between git and status


def test_exact_literal_args_require_exact_match() -> None:
    table = _table(allow=[["foo", "--bar", "--baz"]])
    assert table.evaluate(["foo", "--bar", "--baz"]) == "allow"
    assert table.evaluate(["foo", "--bar"]) is None
    assert table.evaluate(["foo", "--bar", "--baz", "extra"]) is None


def test_no_matching_rule_returns_none() -> None:
    table = _table(allow=[["git", "**"]])
    assert table.evaluate(["ls", "-la"]) is None


# --- deny/ask/allow category precedence ---


def test_deny_beats_allow_regardless_of_specificity() -> None:
    table = _table(deny=[["git", "push", "**"]], allow=[["git", "**"]])
    assert table.evaluate(["git", "status"]) == "allow"
    assert table.evaluate(["git", "push", "origin", "main"]) == "deny"


def test_ask_wins_over_allow_but_loses_to_deny() -> None:
    all_three = _table(deny=[["rm", "**"]], ask=[["rm", "**"]], allow=[["rm", "**"]])
    assert all_three.evaluate(["rm", "-rf", "/"]) == "deny"

    ask_and_allow = _table(ask=[["rm", "**"]], allow=[["rm", "**"]])
    assert ask_and_allow.evaluate(["rm", "-rf", "/"]) == "ask"


def test_matching_rules_reports_every_matching_rule_in_a_category() -> None:
    table = _table(ask=[["git", "**"], ["git", "push", "**"]])
    matches = table.matching_rules("ask", ["git", "push", "origin"])
    assert matches == [["git", "**"], ["git", "push", "**"]]
