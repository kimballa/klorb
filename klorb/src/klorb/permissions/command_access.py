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
                    candidate_index < len(argv) and argv[candidate_index] == token
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

        * A literal token must equal the candidate token at that position exactly.
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
