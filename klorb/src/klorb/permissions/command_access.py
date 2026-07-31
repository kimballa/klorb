# © Copyright 2026 Aaron Kimball
"""Bash-command access control: a `PermissionsTable` resource kind governing which shell
commands `BashTool` (`klorb.tools.bash`) may run, matched against token patterns rather than
canonicalized filesystem paths. See docs/specs/bash-tool-and-command-permissions.md and
docs/specs/permissions.md.

This module has no opinion on how a raw command *string* becomes the `argv`-shaped candidates it
matches against — that's `klorb.permissions.shell_parse`'s job (parsing via `shfmt --to-json` and
walking the resulting AST). `CommandPermissionsTable` only ever sees already-tokenized
`list[str]` candidates, exactly the way `DirectoryAccessTable` only ever sees already-canonicalized
`Path` candidates.
"""

from pydantic import BaseModel, Field

from klorb.permissions.table import PermissionsTable

WILDCARD_TOKEN = "*"
"""A rule token that matches exactly one arbitrary candidate token at that position — always,
regardless of position (including a rule's own last token); see
`CommandPermissionsTable._matches` and
docs/adrs/command-rule-wildcards-double-star-unbounded-anywhere-question-mark-always-optional.md.
A literal token that ends with a trailing `"*"` without being exactly `"*"` (or exactly `"**"`,
`UNBOUNDED_TOKEN`) is a distinct case, a suffix-wildcard literal — see `_token_matches_literal`
and docs/adrs/command-rule-tokens-support-trailing-star-suffix-wildcards.md.
"""

OPTIONAL_TOKEN = "?"
"""A rule token that matches zero or one arbitrary candidate token at that position — uniformly,
regardless of position, including a rule's own last token; see `CommandPermissionsTable._matches`
and
docs/adrs/command-rule-wildcards-double-star-unbounded-anywhere-question-mark-always-optional.md.
"""

UNBOUNDED_TOKEN = "**"
"""A rule token that matches any number of arbitrary candidate tokens at that position, including
zero — at any position in a rule, not just as the last token; see
`CommandPermissionsTable._matches` and
docs/adrs/command-rule-wildcards-double-star-unbounded-anywhere-question-mark-always-optional.md.
"""


def _token_matches_literal(pattern_token: str, candidate_token: str) -> bool:
    """Whether a rule token that is *not* one of the three whole-token wildcards (`WILDCARD_TOKEN`,
    `OPTIONAL_TOKEN`, `UNBOUNDED_TOKEN`) matches a single candidate token, exactly one-for-one.

    Only a `pattern_token`'s own trailing `"*"` ever carries wildcard meaning, and only when it
    isn't the token's entire content (a bare `"*"` is `WILDCARD_TOKEN`, already intercepted by
    `pattern_matches_argv`'s caller before this function ever runs): `"--arg=*"` requires the
    candidate token to start with the literal `"--arg="` prefix, and accepts anything (including
    nothing) after it. A `"*"` anywhere else in `pattern_token` -- not its last character -- is
    just a literal asterisk to match verbatim, not a wildcard: `"a*b"` matches only the literal
    candidate token `"a*b"`. This is a deliberately narrow, single-suffix-wildcard grammar (a
    plain string-prefix check, not a general glob/regex) -- see
    docs/adrs/command-rule-tokens-support-trailing-star-suffix-wildcards.md for why a wildcard anywhere in
    a token (not just trailing) was rejected as unnecessarily permissive.
    """
    if pattern_token.endswith(WILDCARD_TOKEN) and len(pattern_token) > 1:
        return candidate_token.startswith(pattern_token[:-1])
    return pattern_token == candidate_token


def pattern_matches_argv(pattern: list[str], argv: list[str]) -> bool:
    """Whether a single `commandRules` token `pattern` (the `*`/`?`/`**` grammar) matches a
    candidate `argv` (argv0 first) — the exact positional, backtracking match
    `CommandPermissionsTable` applies to each of its own rules, exposed as a standalone pure
    function so a caller holding one candidate pattern can test it without building a whole table.
    `klorb.permissions.risk_classifier` uses it to check that an LLM-suggested grant pattern
    actually matches the command it was proposed for before that pattern is ever shown or
    persisted. See `CommandPermissionsTable._matches` for the full token semantics.
    """
    memo: dict[tuple[int, int], bool] = {}

    def match_from(rule_index: int, candidate_index: int) -> bool:
        key = (rule_index, candidate_index)
        cached = memo.get(key)
        if cached is not None:
            return cached

        if rule_index == len(pattern):
            result = candidate_index == len(argv)
        else:
            token = pattern[rule_index]
            if token == WILDCARD_TOKEN:
                result = candidate_index < len(argv) and match_from(
                    rule_index + 1, candidate_index + 1)
            elif token == OPTIONAL_TOKEN:
                result = match_from(rule_index + 1, candidate_index) or (
                    candidate_index < len(argv)
                    and match_from(rule_index + 1, candidate_index + 1))
            elif token == UNBOUNDED_TOKEN:
                result = match_from(rule_index + 1, candidate_index) or (
                    candidate_index < len(argv)
                    and match_from(rule_index, candidate_index + 1))
            else:
                result = (
                    candidate_index < len(argv)
                    and _token_matches_literal(token, argv[candidate_index])
                    and match_from(rule_index + 1, candidate_index + 1))

        memo[key] = result
        return result

    return match_from(0, 0)


class CommandRules(BaseModel):
    """One `commandRules` config key's `deny`/`ask`/`allow` rule lists, as plain token-pattern
    data — see `CommandPermissionsTable` for the evaluation logic that consumes this. Each rule
    is a `list[str]` of tokens (argv0 first), matched positionally against a parsed simple
    command's own argv — see `CommandPermissionsTable._matches`. Treated as immutable after
    construction, mirroring `DirRules`'s own documented contract: nothing in this codebase
    mutates these lists in place post-construction.
    """

    deny: list[list[str]] = Field(default_factory=list)
    ask: list[list[str]] = Field(default_factory=list)
    allow: list[list[str]] = Field(default_factory=list)


class CommandPermissionsTable(PermissionsTable[list[str]]):
    """A `PermissionsTable` over parsed `argv` token lists (argv0 first): a rule matches a
    candidate per the token-wildcard semantics documented in `_matches`.

    Unlike `DirectoryAccessTable`, rule tokens need no canonicalization step at construction
    time — a command-rule token is either a literal string or a wildcard, not a filesystem path
    with `~`-expansion/symlink-resolution concerns, so `CommandRules`' lists are used as-is.
    """

    def __init__(self, rules: CommandRules) -> None:
        super().__init__(deny=list(rules.deny), ask=list(rules.ask), allow=list(rules.allow))

    def _matches(self, rule: list[str], candidate: list[str]) -> bool:
        """Positional token match, backtracking where `OPTIONAL_TOKEN` (`"?"`) or
        `UNBOUNDED_TOKEN` (`"**"`) is involved:

        * A literal token with no trailing `"*"` must equal the candidate token at that position
          exactly — this includes a `"*"` occurring anywhere *except* as the token's last
          character, which is just a literal asterisk, not a wildcard: `"a*b"` matches only the
          literal candidate token `"a*b"`.
        * A literal token that *ends* with `"*"` without being exactly `"*"` (`WILDCARD_TOKEN`) or
          `"**"` (`UNBOUNDED_TOKEN`) is a suffix-wildcard literal: it still consumes exactly one
          candidate token at that position (never zero, never more), but that token only has to
          start with the pattern token's own prefix (everything before the trailing `"*"`), not
          equal it verbatim. `["dd", "if=/dev/zero", "of=*", "bs=1", "count=*"]` matches
          `["dd", "if=/dev/zero", "of=/home/aaron/zeros.bin", "bs=1", "count=32"]`: `"of=*"` and
          `"count=*"` each still occupy exactly one argv position, they just accept any suffix
          (including none) after their literal prefix rather than requiring an exact match. This is
          a plain string-prefix check on a single token, not a shell glob expansion or a second way
          to span multiple candidate tokens — `"of=*"` never matches across a space the way
          `UNBOUNDED_TOKEN` spans multiple argv entries, and a `"*"` earlier in the same token
          (not its last character) still isn't a wildcard; see `_token_matches_literal` and
          docs/adrs/command-rule-tokens-support-trailing-star-suffix-wildcards.md.
        * `WILDCARD_TOKEN` (`"*"`) matches exactly one arbitrary candidate token at that
          position, always — including when it's the rule's last token: `["foo", "*"]` matches
          `["foo", "bar"]` but not `["foo"]` (zero extra tokens) or `["foo", "bar", "baz"]` (two
          extra tokens). A rule with no wildcard at all matches only a candidate of the exact
          same length: `["foo"]` matches only the bare `foo` invocation, with no arguments.
        * `OPTIONAL_TOKEN` (`"?"`) matches zero or one arbitrary candidate token at that
          position, uniformly regardless of position: `["foo", "?"]` matches `["foo"]` and
          `["foo", "bar"]`, but not `["foo", "bar", "baz"]`. Whether it consumes a token depends
          on what the rest of the pattern needs next, so resolving it requires backtracking:
          `["git", "?", "status"]` matches both `["git", "status"]` (the `"?"` consumes nothing)
          and `["git", "--no-pager", "status"]` (it consumes one token).
        * `UNBOUNDED_TOKEN` (`"**"`) matches any number of arbitrary candidate tokens at that
          position, including zero, at any position in a rule: `["git", "**", "status"]` matches
          `["git", "status"]`, `["git", "-C", "dir", "status"]`, etc., and `["git", "**"]`
          matches any candidate starting with `git`. Like `"?"`, resolving it requires
          backtracking against the rest of the pattern.

        `["git", "*", "status", "*"]` matches `["git", "foo", "status", "bar"]`: each `*`
        consumes exactly one token, neither is optional — a candidate with nothing between `git`
        and `status`, or nothing after the final `status`, does not match this rule.
        """
        return pattern_matches_argv(rule, candidate)
