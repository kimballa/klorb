# © Copyright 2026 Aaron Kimball
"""Tests for klorb.permissions.command_access: CommandRules/CommandPermissionsTable token-
pattern matching and deny/ask/allow precedence. See
docs/specs/bash-tool-and-command-permissions.md,
docs/adrs/command-rule-wildcards-double-star-unbounded-anywhere-question-mark-always-optional.md,
and docs/adrs/command-rule-tokens-support-trailing-star-suffix-wildcards.md.
"""

import shlex

from klorb.permissions.command_access import (
    CommandPermissionsTable,
    CommandRules,
    _token_matches_literal,
    pattern_matches_argv,
)


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


# --- pattern_matches_argv: the standalone matcher CommandPermissionsTable._matches delegates to ---


def test_pattern_matches_argv_agrees_with_the_table_across_the_grammar() -> None:
    """The standalone entry point is the exact matcher `_matches` delegates to, so a single
    `allow` rule's table verdict and the bare `pattern_matches_argv` call must never disagree --
    exercised across every special token so an accidental divergence in the extraction is caught."""
    cases: list[tuple[list[str], list[str]]] = [
        (["foo"], ["foo"]),
        (["foo"], ["foo", "bar"]),
        (["foo", "*"], ["foo", "bar"]),
        (["foo", "*"], ["foo"]),
        (["git", "?", "status"], ["git", "status"]),
        (["git", "?", "status"], ["git", "--no-pager", "status"]),
        (["git", "**", "status", "**"], ["git", "-C", "dir", "status", "-s"]),
        (["git", "push", "**"], ["git", "commit"]),
    ]
    for pattern, argv in cases:
        table_verdict = _table(allow=[pattern]).evaluate(argv)
        assert pattern_matches_argv(pattern, argv) == (table_verdict == "allow")


def test_pattern_matches_argv_rejects_a_pattern_missing_a_required_literal() -> None:
    """A hallucinated abstraction the risk classifier must catch: a pattern that drops or mistypes
    a token the actual command carries does not match that command's argv."""
    assert not pattern_matches_argv(["git", "status"], ["git", "log"])
    assert not pattern_matches_argv(["grep", "-rn", "TODO"], ["grep", "-rn", "TODO", "src/foo.py"])
    assert pattern_matches_argv(["grep", "-rn", "TODO", "**"], ["grep", "-rn", "TODO", "src/foo.py"])


# --- multi-wildcard workouts: `*`/`?`/`**` interleaved -----------------------------------------
#
# These give the token grammar a heavy workout with *many* candidate permutations per rule, since
# the real-world failures reported against `commandRules` are about surprising arity behavior when
# several wildcards combine, not about any single token in isolation. `_admits`/`_rejects` assert
# through BOTH the standalone `pattern_matches_argv` AND an allow-only `CommandPermissionsTable`
# verdict on every candidate, so a divergence between the two paths (or a table-level regression)
# fails here too, not just a matcher bug. See
# docs/adrs/command-rule-wildcards-double-star-unbounded-anywhere-question-mark-always-optional.md.


def _admits(pattern: list[str], *argvs: list[str]) -> None:
    for argv in argvs:
        assert pattern_matches_argv(pattern, argv), f"expected {pattern} to admit {argv}"
        assert _table(allow=[pattern]).evaluate(argv) == "allow", (
            f"table verdict for {pattern} on {argv} should be 'allow'")


def _rejects(pattern: list[str], *argvs: list[str]) -> None:
    for argv in argvs:
        assert not pattern_matches_argv(pattern, argv), f"expected {pattern} to reject {argv}"
        assert _table(allow=[pattern]).evaluate(argv) is None, (
            f"table verdict for {pattern} on {argv} should be None")


# --- suffix-wildcard tokens: a literal token ending in a trailing `*` without being exactly -----
# `"*"` or `"**"` -- see docs/adrs/command-rule-tokens-support-trailing-star-suffix-wildcards.md.
# The risk classifier sometimes proposes exactly this shape (`["dd", "of=*", ...]`) to generalize
# the value half of a `--flag=value`/`key=value` argument while keeping the flag name literal. Only
# a token's own *last* character carries wildcard meaning -- a `*` anywhere else in the same token
# is just a literal asterisk.


def test_suffix_wildcard_token_matches_anything_after_literal_prefix() -> None:
    """`"of=*"` occupies exactly one argv position, same arity as a bare `*`, but the candidate
    token there must start with the literal `of=` prefix -- the `*` only stands in for what comes
    after it, including nothing at all."""
    pattern = ["dd", "of=*"]
    _admits(
        pattern,
        ["dd", "of=/tmp/x"],
        ["dd", "of=/home/aaron/zeros.bin"],
        ["dd", "of="],  # `*` matches zero characters too
    )
    _rejects(
        pattern,
        ["dd"],  # no token at all for `of=*` to consume
        ["dd", "if=/tmp/x"],  # wrong prefix entirely
        ["dd", "iof=/tmp/x"],  # prefix must match from the start of the token, not a substring
        ["dd", "of=/tmp/x", "extra"],  # still exactly one token, no more
    )


def test_suffix_wildcard_token_still_consumes_exactly_one_argv_position() -> None:
    """A suffix wildcard never spans a space into a second candidate token the way `**` does --
    it's a single-token prefix check, not a second unbounded-match mechanism."""
    pattern = ["cmd", "--path=*", "--done"]
    _admits(pattern, ["cmd", "--path=/a/b", "--done"])
    _rejects(
        pattern,
        ["cmd", "--path=/a", "b", "--done"],  # the value can't spill into its own extra token
        ["cmd", "--done"],  # `--path=*` still needs a token of its own
    )


def test_suffix_wildcard_reproduces_the_reported_dd_command() -> None:
    """The exact shape reported against the risk classifier: it proposed
    `["dd", "if=/dev/zero", "of=*", "bs=1", "count=*"]` for a `dd if=/dev/zero
    of=/home/aaron/zeros.bin bs=1 count=32` invocation, and the matcher rejected it outright before
    suffix-wildcard support existed."""
    pattern = ["dd", "if=/dev/zero", "of=*", "bs=1", "count=*"]
    _admits(
        pattern,
        ["dd", "if=/dev/zero", "of=/home/aaron/zeros.bin", "bs=1", "count=32"],
        ["dd", "if=/dev/zero", "of=/tmp/out", "bs=1", "count=1"],
    )
    _rejects(
        pattern,
        ["dd", "if=/dev/random", "of=/tmp/out", "bs=1", "count=1"],  # if= must stay literal
        ["dd", "if=/dev/zero", "of=/tmp/out", "bs=2", "count=1"],  # bs=1 must stay literal
    )


def test_star_not_in_trailing_position_is_a_literal_character() -> None:
    """Only a token's own trailing `*` is a wildcard -- a `*` anywhere earlier in the same token
    is just a literal asterisk character, matched verbatim, never a glob."""
    pattern = ["find", "*.tar"]  # leading `*`, no trailing `*`: a plain literal token
    _admits(pattern, ["find", "*.tar"])
    _rejects(pattern, ["find", "backup.tar"], ["find", "a.tar"])

    pattern_mid_star = ["cmd", "a*b"]  # `*` in the middle, no trailing `*`: also a plain literal
    _admits(pattern_mid_star, ["cmd", "a*b"])
    _rejects(pattern_mid_star, ["cmd", "aXb"], ["cmd", "ab"])


def test_suffix_wildcard_with_a_literal_star_earlier_in_the_same_token() -> None:
    """A token can have a literal `*` earlier and a wildcard `*` as its last character at once --
    only the trailing one is special; the earlier one is still just a literal character that must
    appear verbatim in the matched prefix."""
    pattern = ["cmd", "a*b*"]
    _admits(pattern, ["cmd", "a*b"], ["cmd", "a*bc"], ["cmd", "a*bxyz"])
    _rejects(pattern, ["cmd", "aXb"], ["cmd", "ab"], ["cmd", "a*"])


def test_literal_question_mark_inside_a_token_is_not_a_wildcard() -> None:
    """`OPTIONAL_TOKEN` semantics only apply to a rule token that IS exactly `"?"`; a `"?"`
    embedded inside a larger literal token is just a literal character, requiring an exact
    match -- this grammar never grew glob meaning for `?` or `[...]` the way `fnmatch` would, only
    for a token's own trailing `*` (see
    docs/adrs/command-rule-tokens-support-trailing-star-suffix-wildcards.md)."""
    pattern = ["echo", "really?"]
    _admits(pattern, ["echo", "really?"])
    _rejects(pattern, ["echo", "really1"], ["echo", "reallyX"], ["echo", "really"])


def test_suffix_wildcard_argv0_is_not_privileged() -> None:
    """Suffix-wildcard tokens behave the same in argv0 position as anywhere else at the matcher
    level -- `_has_unsafe_wildcard_argv0` (klorb.permissions.risk_classifier) is what refuses to
    treat this shape as a safe *suggestion*, but the matcher itself matches it uniformly."""
    _admits(["py*", "test"], ["python3", "test"], ["pypy", "test"])
    _rejects(["py*", "test"], ["ruby", "test"], ["python3", "run"])


def test_suffix_wildcard_matches_shlex_parsed_quoted_argument_forms() -> None:
    """The risk classifier's `suggested_pattern` is matched against argv produced by
    `klorb.permissions.shell_parse`'s tokenizer, which -- like `shlex` -- folds a quoted value
    (whether the whole token is quoted or just part of it) into one argv entry with the quotes
    stripped and any embedded space preserved. `"--arg=*"` must match that single entry regardless
    of which of these equivalent shell spellings produced it."""
    pattern = ["prog", "--arg=*"]
    unquoted = shlex.split("prog --arg=foo")
    assert unquoted == ["prog", "--arg=foo"]
    assert pattern_matches_argv(pattern, unquoted)

    partially_quoted = shlex.split('prog --arg="foo bar"')
    assert partially_quoted == ["prog", "--arg=foo bar"]
    assert pattern_matches_argv(pattern, partially_quoted)

    fully_quoted = shlex.split('prog "--arg=foo bar"')
    assert fully_quoted == ["prog", "--arg=foo bar"]
    assert pattern_matches_argv(pattern, fully_quoted)

    assert _table(allow=[pattern]).evaluate(unquoted) == "allow"
    assert _table(allow=[pattern]).evaluate(partially_quoted) == "allow"
    assert _table(allow=[pattern]).evaluate(fully_quoted) == "allow"


def test_suffix_wildcard_rejects_shlex_parsed_argument_with_wrong_prefix() -> None:
    """The prefix before the `*` must still match exactly, even once shlex has folded a quoted
    value into a single token."""
    pattern = ["prog", "--arg=*"]
    argv = shlex.split('prog --other="foo bar"')
    assert argv == ["prog", "--other=foo bar"]
    assert not pattern_matches_argv(pattern, argv)
    assert _table(allow=[pattern]).evaluate(argv) is None


def test_token_matches_literal_plain_equality_when_no_trailing_star_present() -> None:
    """A token with no trailing `*` at all is the ordinary literal branch: exact equality,
    unaffected by suffix-wildcard support -- including a token that merely contains `*`
    somewhere other than its last character."""
    assert _token_matches_literal("foo", "foo")
    assert not _token_matches_literal("foo", "bar")
    assert not _token_matches_literal("foo", "foo ")
    assert _token_matches_literal("a*b", "a*b")
    assert not _token_matches_literal("a*b", "aXb")


def test_token_matches_literal_suffix_wildcard_shapes() -> None:
    """Direct unit coverage of `_token_matches_literal` itself, isolated from
    `pattern_matches_argv`'s positional/backtracking plumbing."""
    assert _token_matches_literal("--arg=*", "--arg=foo")
    assert _token_matches_literal("--arg=*", "--arg=")
    assert not _token_matches_literal("--arg=*", "-arg=foo")
    assert _token_matches_literal("a*b*", "a*bc")  # only the trailing `*` is a wildcard
    assert not _token_matches_literal("a*b*", "aXbc")


def test_consecutive_single_stars_require_exact_arity() -> None:
    """The exact shape from the reported bug: `grep -n * *` is `["grep","-n","*","*"]`, and each
    `*` is exactly one token -- so it matches ONLY a `grep -n` with *precisely two* further args,
    never one, never three, and never a different flag. This is the surprise behind "things I
    think match `grep -n * *` keep asking": it is pure token arity. `shell_parse` does not expand
    globs (shfmt parses `*.py` to a single `Lit` token, never a file list), so whether a real
    invocation lands on exactly two trailing tokens depends entirely on how the model spelled it
    -- `grep -n foo *.py` is two tokens and matches, but `grep -n foo` (one) and
    `grep -n foo a.py b.py` (three) do not, and `-rn` is not `-n`."""
    pattern = ["grep", "-n", "*", "*"]
    _admits(
        pattern,
        ["grep", "-n", "foo", "bar.py"],
        ["grep", "-n", "-i", "needle"],
        ["grep", "-n", "*.py", "*.txt"],  # unexpanded globs are just two literal tokens here
        ["grep", "-n", "pattern", "dir/"],
    )
    _rejects(
        pattern,
        ["grep", "-n"],                              # zero args after -n
        ["grep", "-n", "foo"],                       # one arg: a `*` has nothing to match
        ["grep", "-n", "foo", "a.py", "b.py"],       # three args: last `*` matches only one
        ["grep", "-n", "a", "b", "c", "d"],          # four args
        ["grep", "-rn", "foo", "bar"],               # -rn != -n
        ["grep", "foo", "bar", "baz"],               # no -n token at position 1
        ["rg", "-n", "foo", "bar"],                  # different argv0
    )


def test_star_before_double_star_requires_at_least_one_arg() -> None:
    """`["cmd","*","**"]` -- the `*` is a mandatory single token, the `**` mops up the rest: at
    least one arg required, then any number."""
    pattern = ["cmd", "*", "**"]
    _admits(
        pattern,
        ["cmd", "a"],
        ["cmd", "a", "b"],
        ["cmd", "a", "b", "c", "d", "e"],
    )
    _rejects(pattern, ["cmd"], ["other", "a"])


def test_double_star_before_star_also_requires_at_least_one_arg() -> None:
    """`["cmd","**","*"]` is the mirror of the previous case and must behave identically on arity:
    the `*` still forces one final token no matter how the greedy `**` before it backtracks."""
    pattern = ["cmd", "**", "*"]
    _admits(
        pattern,
        ["cmd", "a"],
        ["cmd", "a", "b"],
        ["cmd", "a", "b", "c"],
    )
    _rejects(pattern, ["cmd"])


def test_consecutive_question_marks_are_a_bounded_optional_range() -> None:
    """`["cmd","?","?"]` -- each `?` is zero-or-one, so together they admit zero, one, OR two
    trailing tokens, but never three. A bounded optional tail, unlike `**`."""
    pattern = ["cmd", "?", "?"]
    _admits(pattern, ["cmd"], ["cmd", "a"], ["cmd", "a", "b"])
    _rejects(pattern, ["cmd", "a", "b", "c"], ["nope"])


def test_star_and_question_mark_combine_to_a_one_or_two_arg_window() -> None:
    """`["cmd","*","?"]` -- mandatory single `*` plus optional `?`: exactly one or two args."""
    pattern = ["cmd", "*", "?"]
    _admits(pattern, ["cmd", "a"], ["cmd", "a", "b"])
    _rejects(pattern, ["cmd"], ["cmd", "a", "b", "c"])


def test_double_star_backtracks_across_a_repeated_literal() -> None:
    """A greedy `**` must give tokens back so a later required literal can still land -- exercised
    with the literal actually repeated in the candidate, the classic case where a naive greedy
    match fails."""
    pattern = ["git", "**", "log", "**"]
    _admits(
        pattern,
        ["git", "log"],                       # both ** empty
        ["git", "log", "log"],                # first ** must yield so the literal matches one "log"
        ["git", "-C", "d", "log", "-p"],
        ["git", "log", "log", "log"],
    )
    _rejects(
        pattern,
        ["git", "commit"],                    # no "log" anywhere
        ["git", "-C", "d"],
    )


def test_double_star_between_two_literals_needs_the_trailing_literal_present() -> None:
    """`["a","**","x","**","y"]` -- two greedy segments around interior literals; the final `y` is
    still mandatory, and `**` never consumes it away to force a spurious match."""
    pattern = ["a", "**", "x", "**", "y"]
    _admits(
        pattern,
        ["a", "x", "y"],                      # both ** empty
        ["a", "p", "x", "q", "y"],
        ["a", "x", "x", "y", "y"],            # ambiguous interior; a valid assignment exists
    )
    _rejects(
        pattern,
        ["a", "x"],                           # missing trailing y
        ["a", "y"],                           # missing interior x
        ["a", "p", "q"],                      # neither interior literal present
    )


def test_wildcards_in_the_argv0_position() -> None:
    """argv0 is not privileged -- a wildcard there behaves like anywhere else."""
    _admits(["*", "status"], ["git", "status"], ["hg", "status"])
    _rejects(["*", "status"], ["status"], ["git", "log", "status"], ["git"])

    _admits(["**", "status"], ["status"], ["git", "status"], ["a", "b", "c", "status"])
    _rejects(["**", "status"], ["git", "log"], ["status", "extra"])

    _admits(["?", "status"], ["status"], ["git", "status"])
    _rejects(["?", "status"], ["git", "log", "status"])


def test_empty_argv_against_each_wildcard_kind() -> None:
    """The empty-tail boundary for each token: `*` needs a token and rejects `[]`; `?` and `**`
    are satisfied by nothing and admit it."""
    _rejects(["*"], [])
    _rejects(["x"], [])
    _admits(["?"], [])
    _admits(["**"], [])
    _admits(["**", "**"], [])   # multiple `**` still collapse to "any, including none"


def test_fully_interleaved_all_three_wildcards() -> None:
    """The heavy one: `["cmd","**","sub","*","?","**"]`. After the mandatory literal `sub` there
    must be at least one token (the `*`); the leading `**`, the `?`, and the trailing `**` are all
    elastic, so the matcher has to backtrack across every combination to find a valid assignment
    (or prove none exists)."""
    pattern = ["cmd", "**", "sub", "*", "?", "**"]
    _admits(
        pattern,
        ["cmd", "sub", "a"],                       # everything elastic empty; * = a
        ["cmd", "x", "y", "sub", "a"],             # leading ** = x,y
        ["cmd", "sub", "a", "b"],                  # ? = b
        ["cmd", "sub", "a", "b", "c", "d"],        # trailing ** = c,d
        ["cmd", "p", "sub", "q", "sub", "a"],      # requires backtracking the leading **
    )
    _rejects(
        pattern,
        ["cmd", "sub"],                            # nothing for the mandatory * after sub
        ["cmd"],                                    # no sub literal
        ["cmd", "a", "b"],                          # sub literal absent entirely
        ["other", "sub", "a"],                     # argv0 mismatch
    )
