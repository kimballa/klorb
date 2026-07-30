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

import re

from pydantic import BaseModel, Field

from klorb.permissions.table import PermissionsTable

WILDCARD_TOKEN = "*"
"""A rule token that matches exactly one arbitrary candidate token at that position — always,
regardless of position (including a rule's own last token); see
`CommandPermissionsTable._matches` and
docs/adrs/command-rule-wildcards-double-star-unbounded-anywhere-question-mark-always-optional.md.
A literal token that merely *embeds* one or more `"*"` characters without being exactly `"*"` (or
exactly `"**"`, `UNBOUNDED_TOKEN`) is a distinct case, an embedded-glob token — see
`_token_matches_literal` and
docs/adrs/command-rule-tokens-support-embedded-glob-wildcards.md.
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

    A `pattern_token` with no `"*"` in it at all is an ordinary literal: exact string equality. A
    `pattern_token` that embeds one or more `"*"` characters (`"--arg=*"`, `"*.py"`) is an
    embedded-glob token: each `"*"` matches any run of characters (including none) within the
    single candidate token, the same way a shell glob's `*` matches within one path segment --
    never expanding to match across multiple candidate tokens the way `UNBOUNDED_TOKEN` does. This
    only ever runs on tokens already ruled out as the exact strings `"*"`/`"?"`/`"**"` by
    `pattern_matches_argv`'s caller, so a bare `"*"` here can only mean an embedded glob with
    literal characters around it.
    """
    if "*" not in pattern_token:
        return pattern_token == candidate_token
    segments = pattern_token.split("*")
    regex = ".*".join(re.escape(segment) for segment in segments)
    return re.fullmatch(regex, candidate_token) is not None


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

        * A literal token with no `"*"` in it must equal the candidate token at that position
          exactly.
        * A literal token that embeds one or more `"*"` characters without being exactly `"*"`
          (`WILDCARD_TOKEN`) or `"**"` (`UNBOUNDED_TOKEN`) is an embedded-glob token: it still
          consumes exactly one candidate token at that position (never zero, never more), but that
          token must match the glob rather than equal it verbatim — each `"*"` inside the pattern
          token matches any run of characters (including none) within the single candidate token.
          `["dd", "if=/dev/zero", "of=*", "bs=1", "count=*"]` matches
          `["dd", "if=/dev/zero", "of=/home/aaron/zeros.bin", "bs=1", "count=32"]`: `"of=*"` and
          `"count=*"` each still occupy exactly one argv position, they just accept any suffix
          after their literal `=` prefix rather than requiring an exact match. This is a single-
          token glob, not a shell glob expansion or a second way to span multiple candidate
          tokens — `"of=*"` never matches across a space the way `UNBOUNDED_TOKEN` spans multiple
          argv entries; see `_token_matches_literal` and
          docs/adrs/command-rule-tokens-support-embedded-glob-wildcards.md.
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
