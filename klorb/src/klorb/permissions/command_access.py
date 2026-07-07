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
docs/adrs/command-rule-wildcards-bounded-star-trailing-unbounded-question-mark.md."""

OPTIONAL_TOKEN = "?"
"""A rule token that matches zero or one arbitrary candidate token at that position — except
when it is the rule's own *last* token, where it instead matches any number of further candidate
tokens, including zero (an unbounded trailing match); see `CommandPermissionsTable._matches` and
docs/adrs/command-rule-wildcards-bounded-star-trailing-unbounded-question-mark.md."""


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
        """Positional token match, backtracking where `OPTIONAL_TOKEN` (`"?"`) is involved:

        * A literal token must equal the candidate token at that position exactly.
        * `WILDCARD_TOKEN` (`"*"`) matches exactly one arbitrary candidate token at that
          position, always — including when it's the rule's last token: `["foo", "*"]` matches
          `["foo", "bar"]` but not `["foo"]` (zero extra tokens) or `["foo", "bar", "baz"]` (two
          extra tokens). A rule with no wildcard at all matches only a candidate of the exact
          same length: `["foo"]` matches only the bare `foo` invocation, with no arguments.
        * `OPTIONAL_TOKEN` (`"?"`) matches zero or one arbitrary candidate token at that
          position — except as the rule's own *last* token, where it instead matches any number
          of further candidate tokens, including zero: `["foo", "?"]` matches `["foo"]`,
          `["foo", "bar"]`, `["foo", "bar", "baz"]`, etc. A non-last `"?"` requires backtracking
          to resolve correctly, since whether it consumes a token depends on what the rest of the
          pattern needs: `["git", "?", "status"]` matches both `["git", "status"]` (the `"?"`
          consumes nothing) and `["git", "--no-pager", "status"]` (it consumes one token).

        `["git", "*", "status", "*"]` matches `["git", "foo", "status", "bar"]`: each `*`
        consumes exactly one token, neither is optional — a candidate with nothing between `git`
        and `status`, or nothing after the final `status`, does not match this rule.
        """
        memo: dict[tuple[int, int], bool] = {}

        def match_from(rule_index: int, candidate_index: int) -> bool:
            key = (rule_index, candidate_index)
            cached = memo.get(key)
            if cached is not None:
                return cached

            if rule_index == len(rule):
                result = candidate_index == len(candidate)
            else:
                token = rule[rule_index]
                is_last = rule_index == len(rule) - 1
                if token == OPTIONAL_TOKEN and is_last:
                    result = True
                elif token == WILDCARD_TOKEN:
                    result = candidate_index < len(candidate) and match_from(
                        rule_index + 1, candidate_index + 1)
                elif token == OPTIONAL_TOKEN:
                    result = match_from(rule_index + 1, candidate_index) or (
                        candidate_index < len(candidate)
                        and match_from(rule_index + 1, candidate_index + 1))
                else:
                    result = (
                        candidate_index < len(candidate) and candidate[candidate_index] == token
                        and match_from(rule_index + 1, candidate_index + 1))

            memo[key] = result
            return result

        return match_from(0, 0)
