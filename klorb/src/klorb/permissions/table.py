# © Copyright 2026 Aaron Kimball
"""Abstract permission-table evaluation: deny/ask/allow rule lists checked in that fixed
category order, independent of what kind of resource (a directory, eventually a bash command
or a URL — see TODO.md's "Permissions" backlog item) the rules describe. See
docs/specs/permissions.md and
docs/adrs/evaluate-permission-categories-deny-then-ask-then-allow.md.
"""

from abc import ABC, abstractmethod
from typing import Generic, Literal, TypeVar

T = TypeVar("T")

Verdict = Literal["deny", "ask", "allow"]
"""The outcome of evaluating one candidate against a `PermissionsTable`: the category of the
first matching rule, checked in that fixed order (deny, then ask, then allow) — rule
specificity never changes this order, so a broad `deny` always beats a narrower `allow`."""


class PermissionAskRequired(Exception):
    """Raised when a `PermissionsTable`'s verdict for a candidate is `"ask"`, and the call site
    has no interactive confirmation plumbing to route the question through yet (see TODO.md's
    "Permissions" backlog item: "Need to handle `PermissionAskRequired` exception with a user
    prompt."). Deliberately not a `PermissionError` subclass, so future interactive-prompt code
    can catch it specifically without also swallowing plain denials. For now, callers that
    raise this are failing closed: treating `"ask"` the same as denial, just distinguishably.
    """


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


def raise_if_not_allowed(verdict: Verdict, *, resource_description: str) -> None:
    """Enforce `verdict`: raise `PermissionError` for `"deny"`, `PermissionAskRequired` for
    `"ask"` (failing closed — see `PermissionAskRequired`), or return normally for `"allow"`.

    This is the single seam where "ask fails closed as deny-but-distinctly-typed" is
    implemented, so swapping it for real interactive confirmation later is a one-function
    change rather than a change at every tool call site.
    """
    if verdict == "deny":
        raise PermissionError(f"Permission denied: {resource_description}")
    if verdict == "ask":
        raise PermissionAskRequired(f"Permission requires confirmation: {resource_description}")
