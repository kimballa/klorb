# © Copyright 2026 Aaron Kimball
"""File access control: concrete `PermissionsTable` resource kind governing read/write access to
individual files by exact path match or `*`-wildcard pattern."""

import re
from pathlib import Path

from pydantic import BaseModel, Field

from klorb.permissions.directory_access import canonicalize_dir
from klorb.permissions.table import PermissionsTable


class FileRules(BaseModel):
    """One file-access direction's (`readFiles` or `writeFiles`) `deny`/`ask`/`allow` rule
    lists, as plain path data. Treated as immutable after construction.
    """

    deny: list[Path] = Field(default_factory=list)
    ask: list[Path] = Field(default_factory=list)
    allow: list[Path] = Field(default_factory=list)


def is_wildcard_rule(rule: Path) -> bool:
    """Whether `rule`'s string form contains the `*` wildcard metacharacter."""
    return "*" in str(rule)


def _wildcard_pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a rule string containing `*` into a regex matching it against a full candidate
    path string: `*` stands for zero or more of any character, including `/` (there's no `**`
    vs. `*` distinction here), and every other
    character is matched literally, including other glob metacharacters like `?` or `[...]`,
    which carry no special meaning in this grammar.
    """
    return re.compile("^" + ".*".join(re.escape(segment) for segment in pattern.split("*")) + "$")


class FileAccessTable(PermissionsTable[Path]):
    """A `PermissionsTable` over canonicalized individual file paths: a rule with no `*` in it
    matches a candidate only when it names the exact same file. A rule
    containing `*` matches by pattern instead.
    """

    def __init__(self, rules: FileRules, workspace_root: Path) -> None:
        super().__init__(
            deny=[canonicalize_dir(path, workspace_root) for path in rules.deny],
            ask=[canonicalize_dir(path, workspace_root) for path in rules.ask],
            allow=[canonicalize_dir(path, workspace_root) for path in rules.allow],
        )

    def _matches(self, rule: Path, candidate: Path) -> bool:
        if not is_wildcard_rule(rule):
            return candidate == rule
        return _wildcard_pattern_to_regex(str(rule)).fullmatch(str(candidate)) is not None
