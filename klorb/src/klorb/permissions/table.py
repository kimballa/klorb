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

from klorb.permissions.resource import (
    BashCommandContext,
    DomainResource,
    PathResource,
    PermissionResource,
    SkillResource,
    StructuralResource,
)

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

    `path`/`is_write`, `skill`, and `url` carry the resolved candidate a caller with interactive
    plumbing (see `Session.on_permission_ask`) can act on directly, without re-parsing
    `resource_description` -- `path`/`is_write` for a directory-access ask, `skill` (a
    `(namespace, name)` pair) for a skill-activation ask (raised by
    `ActivateSkill`/`ReadSkillFile` -- see docs/specs/skills.md), `url` for a `WebFetch` domain
    ask. At most one is ever given; all three are optional so a caller that only cares about the
    message string can omit them. `resource`, computed from whichever one was given (or a
    `klorb.permissions.resource.StructuralResource` built from `message` if none was), is the
    `klorb.permissions.resource.PermissionResource` a caller should actually use -- see that
    module for the polymorphic methods it exposes in place of branching on `path`/`skill`/`url`.
    """

    def __init__(
        self, message: str, *, path: Path | None = None, is_write: bool = False,
        skill: tuple[str, str] | None = None, url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.path = path
        self.is_write = is_write
        self.skill = skill
        self.url = url
        if path is not None:
            self.resource: PermissionResource = PathResource(path=path, is_write=is_write)
        elif skill is not None:
            self.resource = SkillResource(skill_id=skill)
        elif url is not None:
            self.resource = DomainResource(url=url)
        else:
            self.resource = StructuralResource(reason=message)


class PermissionAskItem:
    """One individual resource within a `MultiPermissionAskRequired`, needing its own ask/deny
    decision — a single tool call can raise several of these at once (e.g. `BashTool`: several
    simple commands and/or redirection targets found within one parsed shell command).

    `resource` is the `klorb.permissions.resource.PermissionResource` this item is about --
    `klorb.permissions.resource.StructuralResource` for a structural item (the walker couldn't
    classify something at all), which has no persistable rule of its own, so only "once"/"deny"
    make sense for it, never a session/workspace/homedir grant.

    `bash_context`, when set, is the `klorb.permissions.resource.BashCommandContext` a `BashTool`
    call originated from — set on every ask item that call produces, regardless of `resource`'s
    own kind, since a compound command can need several independent decisions (one per simple
    command and/or redirect target) that all still belong to the same one command the user
    actually typed or the model actually submitted. Purely a display aid for a UI (`klorb.tui.
    panels.permission_ask_panel.PermissionAskPanel`) to show what's actually being run, on top of
    `resource_description`'s per-item specific detail — never itself the resource a grant is
    checked or persisted against, unlike `resource`.
    """

    def __init__(
        self, resource_description: str, *, resource: PermissionResource,
        bash_context: BashCommandContext | None = None,
    ) -> None:
        self.resource_description = resource_description
        self.resource = resource
        self.bash_context = bash_context


class MultiPermissionAskRequired(Exception):
    """Raised when a single tool call's verdict computation identifies multiple independent
    resources needing ask confirmation. Unlike `PermissionAskRequired`, an item here whose
    `resource.is_persistable` is `False` (a structural ask, see
    `klorb.permissions.resource.StructuralResource`) is *not* automatically failed closed —
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
    path: Path | None = None, is_write: bool = False, skill: tuple[str, str] | None = None,
    url: str | None = None,
) -> None:
    """Enforce `verdict`: raise `PermissionError` for `"deny"`, `PermissionAskRequired` for
    `"ask"` (failing closed — see `PermissionAskRequired`), or return normally for `"allow"`.

    This is the single seam where "ask fails closed as deny-but-distinctly-typed" is
    implemented, so swapping it for real interactive confirmation later is a one-function
    change rather than a change at every tool call site. `path`/`is_write`/`skill`/`url` are
    forwarded onto `PermissionAskRequired` so interactive callers (see
    `Session.on_permission_ask`) can act on the specific resource; all are optional, so callers
    that don't have them handy (or don't need them) can omit them.
    """
    if verdict == "deny":
        raise PermissionError(f"Permission denied: {resource_description}")
    if verdict == "ask":
        raise PermissionAskRequired(
            f"Permission requires confirmation: {resource_description}",
            path=path, is_write=is_write, skill=skill, url=url)
