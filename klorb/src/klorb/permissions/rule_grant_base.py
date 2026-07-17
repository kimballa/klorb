# © Copyright 2026 Aaron Kimball
"""Generic single-table permission-rule persistence: the "load a config file's one
`sessionDefaults` rules key, mutate one `deny`/`ask`/`allow` category, write it back" scaffolding
shared by every simple rules kind with that shape -- see `klorb.permissions.command_grant`/
`klorb.permissions.skill_grant`, which each instantiate a `RuleGrantWriter` parameterized on
their own rules type, config key, and (de)serializers.

`klorb.permissions.grant` (directory rules) is not built on this: a directory grant must keep
`readDirs` and `writeDirs` in sync together as a pair, and dedupes by canonicalized-path
equality rather than plain equality -- a two-table, canonicalizing shape this single-table,
plain-equality base doesn't cover.
"""

from pathlib import Path
from typing import Any, Callable, Generic, Protocol, TypeVar

from klorb.permissions.grant import GrantAction
from klorb.process_config import CONFIG_SCHEMA_NAME, CONFIG_SCHEMA_VERSION, SESSION_DEFAULTS_KEY
from klorb.schema_envelope import read_versioned_json, write_versioned_json


class _RulesLike(Protocol):
    """The shape every rules kind a `RuleGrantWriter` supports must have: an immutable-by-
    convention `deny`/`ask`/`allow` triple of same-shaped entries."""

    deny: list[Any]
    ask: list[Any]
    allow: list[Any]


RulesT = TypeVar("RulesT", bound=_RulesLike)


def apply_decision_to_rules(
    rules: RulesT, granted: list[Any], action: GrantAction,
    make: Callable[[list[Any], list[Any], list[Any]], RulesT],
) -> RulesT:
    """Return a NEW rules object (built via `make(deny, ask, allow)`): every entry in `granted`
    appended to `action`'s own category (deduped against its existing entries), and any `ask`
    entry equal to one of `granted` removed. The *other* category is left untouched -- an
    "Allow, always" decision never strips an existing `deny` entry (which would be a security
    regression if a stricter admin-level deny already existed -- `deny` still wins via category
    order regardless), and a "Deny, always" decision never strips an existing `allow` entry (the
    new `deny` entry already wins on its own). Never mutates `rules` in place.
    """
    target = rules.allow if action == "allow" else rules.deny
    new_target = list(target)
    for entry in granted:
        if entry not in new_target:
            new_target.append(entry)
    new_ask = [entry for entry in rules.ask if entry not in granted]
    new_deny = new_target if action == "deny" else list(rules.deny)
    new_allow = new_target if action == "allow" else list(rules.allow)
    return make(new_deny, new_ask, new_allow)


class RuleGrantWriter(Generic[RulesT]):
    """Loads/merges/persists one `sessionDefaults` rules key across a config file, given how to
    build and (de)serialize `RulesT`. `klorb.permissions.command_grant`/`klorb.permissions.
    skill_grant` each own one instance, parameterized on their own rules type.
    """

    def __init__(
        self, *, config_key: str,
        make: Callable[[list[Any], list[Any], list[Any]], RulesT],
        from_json: Callable[[dict[str, Any]], RulesT],
        to_json: Callable[[RulesT], Any],
    ) -> None:
        self._config_key = config_key
        self._make = make
        self._from_json = from_json
        self._to_json = to_json

    def load_file_rules(self, path: Path) -> tuple[dict[str, Any], RulesT]:
        """Read `path`'s own raw `sessionDefaults[config_key]` (`{}` if `path` doesn't exist --
        via `read_versioned_json`), returned as `(full_raw_contents, rules)` so the caller can
        write the file back with only this one key replaced, preserving every other key."""
        raw = read_versioned_json(path, expected_schema_name=CONFIG_SCHEMA_NAME)
        session_defaults = raw.get(SESSION_DEFAULTS_KEY, {})
        return raw, self._from_json(session_defaults.get(self._config_key, {}))

    def write_file_rules(self, path: Path, raw_contents: dict[str, Any], rules: RulesT) -> None:
        """Write `raw_contents` back to `path` with `sessionDefaults[config_key]` replaced by
        `rules`, preserving every other key untouched. Creates `path`'s parent directory and a
        minimal schema envelope if `path` didn't exist yet."""
        session_defaults = dict(raw_contents.get(SESSION_DEFAULTS_KEY, {}))
        session_defaults[self._config_key] = self._to_json(rules)
        new_contents = dict(raw_contents)
        new_contents[SESSION_DEFAULTS_KEY] = session_defaults
        write_versioned_json(
            path, new_contents, schema_name=CONFIG_SCHEMA_NAME, schema_version=CONFIG_SCHEMA_VERSION)

    def apply_decision(self, rules: RulesT, granted: list[Any], action: GrantAction) -> RulesT:
        """Return a NEW rules object with `action`'s decision for `granted` applied in memory
        (see `apply_decision_to_rules`), without touching any file -- what
        `apply_command_permission_grant`/`apply_skill_permission_grant` use to update the live
        `SessionConfig`/`ProcessConfig` before (for a persistent scope) also persisting to disk."""
        return apply_decision_to_rules(rules, granted, action, self._make)

    def apply_grant_to_file(self, path: Path, granted: list[Any], action: GrantAction) -> None:
        """Load `path`'s rules, apply `action`'s decision for `granted` (see
        `apply_decision_to_rules`), and write the result back."""
        raw, rules = self.load_file_rules(path)
        new_rules = apply_decision_to_rules(rules, granted, action, self._make)
        self.write_file_rules(path, raw, new_rules)

    def clean_ask_entries_only(self, path: Path, granted: list[Any]) -> None:
        """Best-effort: if `path` exists and its own rules' `ask` category contains an entry in
        `granted`, remove it and write the file back -- WITHOUT adding anything to either
        `allow`/`deny`. A no-op if `path` doesn't exist or nothing matches."""
        if not path.is_file():
            return
        raw, rules = self.load_file_rules(path)
        new_ask = [entry for entry in rules.ask if entry not in granted]
        if new_ask == rules.ask:
            return
        new_rules = self._make(list(rules.deny), new_ask, list(rules.allow))
        self.write_file_rules(path, raw, new_rules)
