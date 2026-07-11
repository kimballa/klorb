# © Copyright 2026 Aaron Kimball
"""Abstract permission-table evaluation: deny/ask/allow rule lists checked in that fixed
category order, independent of what kind of resource (a directory, or in principle any other
resource kind with its own matching semantics) the rules describe. See
docs/specs/permissions.md and
docs/adrs/evaluate-permission-categories-deny-then-ask-then-allow.md.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Generic, Literal, TypeVar

T = TypeVar("T")

Verdict = Literal["deny", "ask", "allow"]
"""The outcome of evaluating one candidate against a `PermissionsTable`: the category of the
first matching rule, checked in that fixed order (deny, then ask, then allow) — rule
specificity never changes this order, so a broad `deny` always beats a narrower `allow`."""


class PermissionAskRequired(Exception):
    """Raised when a `PermissionsTable`'s verdict for a candidate is `"ask"`. Deliberately not a
    `PermissionError` subclass, so interactive-prompt code can catch it specifically without
    also swallowing plain denials. A call site with no interactive confirmation plumbing (e.g.
    a one-shot/headless run) fails closed: `Session._run_tool_calls` treats an uncaught instance
    of this the same as denial, just distinguishably.

    `path` and `is_write` carry the resolved candidate path and whether this was a write
    (rather than read) interrogation, so a caller with interactive plumbing (see
    `Session.on_permission_ask`, `klorb.permissions.grant`) can act on the specific resource
    without re-parsing `resource_description`. Both are optional and default to `None`/`False`
    so existing callers that only care about the message string are unaffected.
    """

    def __init__(self, message: str, *, path: Path | None = None, is_write: bool = False) -> None:
        super().__init__(message)
        self.path = path
        self.is_write = is_write


class PermissionAskItem:
    """One individual resource within a `MultiPermissionAskRequired`, needing its own ask/deny
    decision — a single tool call can raise several of these at once (e.g. `BashTool`: several
    simple commands and/or redirection targets found within one parsed shell command).

    Exactly one of `path`/`command` is set, or neither: `path` (plus `is_write`) for a
    directory-access item, matching `PermissionAskRequired`'s own shape; `command` (an argv
    pattern) for a bash-command-rule item with no filesystem resource; neither for a structural
    item (the walker couldn't classify something at all) that has no persistable rule of its own
    — only "once"/"deny" make sense for that last case, never a session/workspace/homedir grant.

    `command_text`, when set, is the full, unparsed command string a `BashTool` call originated
    from — set on every ask item that call produces, regardless of which of `path`/`command`/
    neither it also carries, since a compound command can need several independent decisions
    (one per simple command and/or redirect target) that all still belong to the same one command
    the user actually typed or the model actually submitted. Purely a display aid for a
    UI (`klorb.tui.permission_ask_panel.PermissionAskPanel`) to show what's actually being run,
    on top of `resource_description`'s per-item specific detail — never itself the resource a
    grant is checked or persisted against, unlike `path`/`command`.

    `is_compound`, set alongside `command_text` by `BashTool._classify` (`True` when the parsed
    command contains more than one simple command — `foo && bar`, `foo; bar`, `foo | bar`, etc.),
    tells a UI that `resource_description` names only one piece of a larger command line the user
    still needs the full picture of, even when `command_text` itself is short enough to show
    without truncation — see `PermissionAskPanel`'s command-preview logic.

    `item_command_text`, also set alongside `command_text`, is the exact original source text of
    just the one statement *this* item is actually about (a `klorb.permissions.shell_parse.
    SimpleCommand`/`ForcedAskReason`/`RedirectTarget`'s own `source_text`) — distinct from
    `command_text`, which is always the *whole* raw command every item from the same call shares
    identically. A UI should show `item_command_text` as its prominent, per-item preview (falling
    back to `command_text` if unset, e.g. for a non-`BashTool` ask item) and reserve
    `command_text` for the `[more...]`-reachable full picture: for a compound command
    (`echo $SHELL; echo $HOME`), showing `command_text` in the per-item preview would make every
    item's approval request look identical, with no way to tell which one is actually about
    which command.
    """

    def __init__(
        self, resource_description: str, *,
        path: Path | None = None, is_write: bool = False, command: list[str] | None = None,
        command_text: str | None = None, is_compound: bool = False,
        item_command_text: str | None = None,
    ) -> None:
        self.resource_description = resource_description
        self.path = path
        self.is_write = is_write
        self.command = command
        self.command_text = command_text
        self.is_compound = is_compound
        self.item_command_text = item_command_text


class PermissionOverride:
    """A one-shot "Allow (once)" bypass for a single retried tool call — the runtime counterpart
    to `Session.PermissionDecision(action="allow", scope="once")` — persisting no rule-table
    entry, so the identical resource asks again the next time it's encountered.

    A single tool call can carry several once-scoped resources at once (see
    `MultiPermissionAskRequired`), so each field is a set rather than a single value: `paths`
    mirrors `PermissionAskItem.path` (checked via membership, e.g.
    `klorb.permissions.workspace.evaluate_write`), `commands` mirrors `PermissionAskItem.command`
    (each entry the exact argv tuple that was asked about, not a pattern — checked via membership
    in `klorb.tools.bash.BashTool._classify`), and `reasons` mirrors a structural item's
    `resource_description` (the deterministic forced-ask reason text `klorb.permissions.
    shell_parse.parse_command` produces for the same command on every parse, since a structural
    item has no other stable identifier to bypass by).
    """

    def __init__(
        self, *,
        paths: frozenset[Path] = frozenset(),
        commands: frozenset[tuple[str, ...]] = frozenset(),
        reasons: frozenset[str] = frozenset(),
    ) -> None:
        self.paths = paths
        self.commands = commands
        self.reasons = reasons

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PermissionOverride):
            return NotImplemented
        return (
            self.paths == other.paths
            and self.commands == other.commands
            and self.reasons == other.reasons)

    def __repr__(self) -> str:
        return (
            f"PermissionOverride(paths={self.paths!r}, commands={self.commands!r}, "
            f"reasons={self.reasons!r})")


class MultiPermissionAskRequired(Exception):
    """Raised when a single tool call's verdict computation identifies multiple independent
    resources needing ask confirmation. Unlike `PermissionAskRequired`, an item here with no
    `path` (a bare command-pattern or structural ask) is *not* automatically failed closed —
    `Session._run_tool_calls` asks about every item in `items`, in order, via the same
    `on_permission_ask` callback a single-resource ask uses, stopping at the first "deny" answer
    (short-circuit: the whole tool call is denied, no further items are asked about). A
    `permission_framework` of `"deny"` fails every item closed without asking; `"auto"`
    auto-approves every item with an in-memory `"session"`-scope grant, the same as a single
    `PermissionAskRequired` does.
    """

    def __init__(self, message: str, *, items: list[PermissionAskItem]) -> None:
        super().__init__(message)
        self.items = items


class PermissionsTable(ABC, Generic[T]):
    """Governs access to one kind of resource via three rule lists — `deny`, `ask`, `allow` —
    evaluated in that fixed category order: the first category with any matching rule wins,
    regardless of how specific that rule is relative to a non-matching rule in a
    lower-precedence category.
    """

    def __init__(self, *, deny: list[T], ask: list[T], allow: list[T]) -> None:
        self._deny = deny
        self._ask = ask
        self._allow = allow

    @abstractmethod
    def _matches(self, rule: T, candidate: T) -> bool:
        """Return whether `rule` matches `candidate`, per this resource kind's own matching
        semantics (e.g. directory containment for `DirectoryAccessTable`)."""

    def evaluate(self, candidate: T) -> Verdict | None:
        """Return the verdict for `candidate`: the category of the first matching rule, checked
        in deny/ask/allow order, or `None` if nothing in any list matches — the caller is
        responsible for applying its own fallback default in that case.
        """
        categories: tuple[tuple[Verdict, list[T]], ...] = (
            ("deny", self._deny), ("ask", self._ask), ("allow", self._allow))
        for verdict, rules in categories:
            if any(self._matches(rule, candidate) for rule in rules):
                return verdict
        return None

    def matching_rules(self, category: Verdict, candidate: T) -> list[T]:
        """Return every rule in `category` (`deny`/`ask`/`allow`) that matches `candidate`, in
        list order. Unlike `evaluate()`, which only reports the winning category, this exposes
        which specific rule(s) matched — used by `klorb.permissions.grant` to promote a matched
        `ask` rule's own path to `allow` rather than a narrower resolved candidate path.
        """
        rules = {"deny": self._deny, "ask": self._ask, "allow": self._allow}[category]
        return [rule for rule in rules if self._matches(rule, candidate)]


_VERDICT_STRICTNESS: dict[Verdict, int] = {"deny": 0, "ask": 1, "allow": 2}


def stricter_verdict(a: Verdict, b: Verdict) -> Verdict:
    """The more restrictive of two verdicts: `deny` beats `ask` beats `allow`. The single shared
    implementation of this ordering — `klorb.permissions.workspace.evaluate_write` (combining a
    `readDirs`/`writeDirs` verdict pair) and `klorb.tools.bash.BashTool` (combining every simple
    command's/redirection's own verdict for one parsed shell command) both fold their
    contributions down via this same function, rather than each re-deriving the deny-beats-ask-
    beats-allow ordering independently."""
    return a if _VERDICT_STRICTNESS[a] <= _VERDICT_STRICTNESS[b] else b


def raise_if_not_allowed(
    verdict: Verdict, *, resource_description: str,
    path: Path | None = None, is_write: bool = False,
) -> None:
    """Enforce `verdict`: raise `PermissionError` for `"deny"`, `PermissionAskRequired` for
    `"ask"` (failing closed — see `PermissionAskRequired`), or return normally for `"allow"`.

    This is the single seam where "ask fails closed as deny-but-distinctly-typed" is
    implemented, so swapping it for real interactive confirmation later is a one-function
    change rather than a change at every tool call site. `path`/`is_write` are forwarded onto
    `PermissionAskRequired` so interactive callers (see `Session.on_permission_ask`) can act on
    the specific resource; both are optional, so callers that don't have them handy (or don't
    need them) can omit them.
    """
    if verdict == "deny":
        raise PermissionError(f"Permission denied: {resource_description}")
    if verdict == "ask":
        raise PermissionAskRequired(
            f"Permission requires confirmation: {resource_description}", path=path, is_write=is_write)
