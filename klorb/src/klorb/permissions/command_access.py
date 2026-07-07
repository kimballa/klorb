# © Copyright 2026 Aaron Kimball
"""Bash-command access control: the second concrete `PermissionsTable` resource kind (after
`klorb.permissions.directory_access`'s directory access), governing which shell commands
`BashTool` (`klorb.tools.bash`) may run. See
docs/plans/ready/004-bash-permissions-and-bash-tool.md's "`CommandRules` parsing / matching with
shfmt" section and docs/specs/permissions.md.

This module has no opinion on how a raw command *string* becomes the `argv`-shaped candidates it
matches against — that's `klorb.permissions.shell_parse`'s job (parsing via `shfmt --to-json` and
walking the resulting AST). `CommandPermissionsTable` only ever sees already-tokenized
`list[str]` candidates, exactly the way `DirectoryAccessTable` only ever sees already-canonicalized
`Path` candidates.
"""

from pydantic import BaseModel, Field

from klorb.permissions.table import PermissionsTable

WILDCARD_TOKEN = "*"
"""A rule token that matches exactly one arbitrary candidate token at that position — except
when it is the rule's own last token, where it additionally means "and any further tokens,
including zero"; see `CommandPermissionsTable._matches`."""


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
    time — a command-rule token is either a literal string or the `*` wildcard, not a filesystem
    path with `~`-expansion/symlink-resolution concerns, so `CommandRules`' lists are used
    as-is.
    """

    def __init__(self, rules: CommandRules) -> None:
        super().__init__(deny=list(rules.deny), ask=list(rules.ask), allow=list(rules.allow))

    def _matches(self, rule: list[str], candidate: list[str]) -> bool:
        """Positional token match, per the plan's "Concrete pattern representation": each rule
        token is either a literal (matched exactly against the candidate token at that
        position) or `WILDCARD_TOKEN` (`"*"`), which matches exactly one arbitrary candidate
        token at that position — except when it is the rule's *last* token, where it instead
        means "and any further candidate tokens, including zero", so `["foo", "*"]` matches
        `["foo"]`, `["foo", "bar"]`, `["foo", "bar", "baz"]`, etc. A rule with no `*` at all
        matches only a candidate of the exact same length (`["foo"]` matches only the bare
        `foo` invocation, with no arguments at all — deliberately different from `["foo", "*"]`,
        per the plan's "argv0, with forcibly no args" case).

        `["git", "*", "status", "*"]` (`"argv0 any-args someTool any-more-args"`) matches
        `["git", "foo", "status", "bar"]`: the middle `*` consumes exactly one token (`"foo"`),
        it is not optional — a candidate with nothing between `git` and `status` does not match
        this rule.
        """
        candidate_index = 0
        for position, token in enumerate(rule):
            is_last = position == len(rule) - 1
            if token == WILDCARD_TOKEN and is_last:
                return True
            if candidate_index >= len(candidate):
                return False
            if token == WILDCARD_TOKEN:
                candidate_index += 1
                continue
            if candidate[candidate_index] != token:
                return False
            candidate_index += 1
        return candidate_index == len(candidate)
