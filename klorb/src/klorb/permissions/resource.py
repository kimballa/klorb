# © Copyright 2026 Aaron Kimball
"""The kinds of resource a permission ask, grant, or once-scope override can be about:
`PathResource` (directory access), `CommandResource` (a bash argv), `SkillResource`,
`DomainResource`, and `StructuralResource` (a `BashTool` item the shell walker couldn't classify
into a specific rule, so it has no persistable rule of its own). `klorb.permissions.table.
PermissionAskItem`/`PermissionAskRequired` and `klorb.session.events.PermissionAskContext` each
carry exactly one `PermissionResource` instance rather than a set of mutually-exclusive optional
fields, so a caller acts on whichever concrete resource it has via these methods instead of
branching on which field happens to be set. See docs/plans/archive/014-permission-resource-hierarchy.md
for the design this module implements.

`PermissionOverride` (a one-shot "Allow (once)" bypass) lives here too, alongside the resource
kinds its five fields mirror one-for-one.

Every concrete subclass's `apply_grant`/`grant_preview` bodies import their kind's grant module
(`klorb.permissions.grant`/`command_grant`/`skill_grant`/`domain_grant`) locally, inside the
method, rather than at module scope: those modules import `SessionConfig` from `klorb.session`,
which itself imports `klorb.permissions.table` (for `PermissionAskItem`) -- and `table.py` imports
this module for `PermissionResource` -- so a module-level import here would be circular. The same
applies to `klorb.permissions.domain_access.parse_domain`, imported locally inside
`DomainResource`, since `domain_access.py` itself imports `klorb.permissions.table`.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-checking-only: see this module's own docstring for why a real import would cycle.
    from klorb.permissions.grant import GrantAction, GrantScope
    from klorb.process_config import ProcessConfig
    from klorb.session import SessionConfig


@dataclass(frozen=True)
class GrantPreview:
    """What a persistent grant for one `PermissionResource` would actually cover, for a UI to
    show the user up front, before they pick a scope. `block`, set only by `PathResource` (whose
    covered directory list can be long), means `resource_text` should render on its own line
    below explanatory prose; every other kind reads naturally inline with the surrounding
    sentence."""

    resource_text: str
    block: bool = False


class PermissionOverride:
    """A one-shot "Allow (once)" bypass for a single retried tool call -- the runtime counterpart
    to `klorb.session.events.PermissionDecision(action="allow", scope="once")` -- persisting no
    rule-table entry, so the identical resource asks again the next time it's encountered.

    A single tool call can carry several once-scoped resources at once (see
    `klorb.permissions.table.MultiPermissionAskRequired`), so each field is a set rather than a
    single value: `paths` mirrors `PathResource.path` (checked via membership, e.g.
    `klorb.permissions.workspace.evaluate_write`), `commands` mirrors `CommandResource.argv`
    (each entry the exact argv tuple that was asked about, not a pattern -- checked via
    membership in `klorb.tools.bash.BashTool._classify`), `reasons` mirrors
    `StructuralResource.reason` (the deterministic forced-ask reason text
    `klorb.permissions.shell_parse.parse_command` produces for the same command on every parse,
    since a structural item has no other stable identifier to bypass by), `skills` mirrors
    `SkillResource.skill_id` (checked via membership in
    `klorb.tools.skill.common.raise_if_skill_not_allowed`), and `domains` mirrors
    `DomainResource.domain` (checked via membership in `klorb.tools.web.fetch`).
    """

    def __init__(
        self, *,
        paths: frozenset[Path] = frozenset(),
        commands: frozenset[tuple[str, ...]] = frozenset(),
        reasons: frozenset[str] = frozenset(),
        skills: frozenset[tuple[str, str]] = frozenset(),
        domains: frozenset[str] = frozenset(),
    ) -> None:
        self.paths = paths
        self.commands = commands
        self.reasons = reasons
        self.skills = skills
        self.domains = domains

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PermissionOverride):
            return NotImplemented
        return (
            self.paths == other.paths
            and self.commands == other.commands
            and self.reasons == other.reasons
            and self.skills == other.skills
            and self.domains == other.domains)

    def __repr__(self) -> str:
        return (
            f"PermissionOverride(paths={self.paths!r}, commands={self.commands!r}, "
            f"reasons={self.reasons!r}, skills={self.skills!r}, domains={self.domains!r})")


@dataclass(frozen=True)
class BashCommandContext:
    """Display context every ask item from one `BashTool` call shares identically, regardless of
    that item's own `PermissionResource` kind: `command_text` is the full, unparsed command
    string; `is_compound` is `True` when the parsed command contains more than one simple command
    (`foo && bar`, `foo; bar`, `foo | bar`, etc.); `item_command_text` is the exact source text of
    just the one statement this particular item is about, distinct from `command_text`'s whole
    raw command -- `klorb.tui.panels.permission_ask_panel.PermissionAskPanel`'s command-preview
    logic prefers this over `command_text`, falling back to `command_text` when unset; `intent`
    is the model's own short statement of what the whole command is trying to accomplish (see
    docs/specs/bash-tool-and-command-permissions.md's "Agent-stated intent" section), shown as an
    "Intent: ..." line only when set. `BashTool` always supplies `command_text`/`item_command_text`
    (required) and `intent` (a required tool argument), so `item_command_text`/`intent` are
    optional here only for a caller with no real BashTool item to describe. `None` on a
    `PermissionAskItem`/`PermissionAskContext` for any ask that didn't originate from `BashTool`
    at all.
    """

    command_text: str
    is_compound: bool = False
    item_command_text: str | None = None
    intent: str | None = None


class PermissionResource(ABC):
    """One concrete resource kind a permission ask, grant, or once-scope override is about:
    `PathResource`, `CommandResource`, `SkillResource`, `DomainResource`, or `StructuralResource`.
    """

    @abstractmethod
    def header_kind(self) -> str:
        """Short noun phrase for `PermissionAskPanel`'s "Permission requested: <kind>" header."""

    @abstractmethod
    def preview_text(self) -> str | None:
        """The one-line resource preview `PermissionAskPanel` shows below the header when this
        ask has no `BashCommandContext` of its own to preview instead (a path, a
        "/<name> (<namespace>)" skill reference, or a URL). `None` for a resource with nothing of
        its own to preview: `CommandResource` (previewed via `BashCommandContext` instead, since
        every command-kind ask originates from `BashTool`) and `StructuralResource`."""

    @property
    def is_persistable(self) -> bool:
        """Whether this resource has a rule a `"session"`/`"workspace"`/`"homedir"` grant can be
        recorded against. `True` for every kind except `StructuralResource`, which names no
        filesystem path, command pattern, skill, or domain -- only `"once"`/deny make sense for
        it."""
        return True

    @abstractmethod
    def grant_preview(self, session_config: "SessionConfig") -> GrantPreview | None:
        """What a persistent grant for this resource would actually cover, computed ahead of the
        user's choice so a UI can name the real scope up front. `None` only for
        `StructuralResource`, which has nothing persistable to preview."""

    @abstractmethod
    def apply_grant(
        self, action: "GrantAction", scope: "GrantScope",
        session_config: "SessionConfig", process_config: "ProcessConfig | None",
        *, grant_patterns: list[list[str]] | None = None,
    ) -> None:
        """Persist `action`/`scope` for this resource. `grant_patterns` is meaningful only for
        `CommandResource` -- `klorb.session.events.PermissionDecision.grant_patterns` threaded
        straight through when a UI's own displayed grant pattern (e.g. a risk-classifier
        suggestion) must be recorded verbatim instead of recomputed; every other kind ignores it.
        A no-op for `StructuralResource`."""

    @abstractmethod
    def added_to_override(self, override: PermissionOverride) -> PermissionOverride:
        """A new `PermissionOverride` with this resource added to whichever of its five fields
        matches this resource's own kind, leaving every other field as `override` already had
        it."""


@dataclass(frozen=True)
class PathResource(PermissionResource):
    """A directory-access ask: `path` is the resolved candidate a tool tried to read or write,
    `is_write` distinguishes a write attempt from a read."""

    path: Path
    is_write: bool = False

    def header_kind(self) -> str:
        return "Write file" if self.is_write else "Read file"

    def preview_text(self) -> str | None:
        return str(self.path)

    def grant_preview(self, session_config: "SessionConfig") -> GrantPreview:
        from klorb.permissions.grant import compute_grant_paths
        paths = compute_grant_paths(
            session_config.read_dirs, session_config.write_dirs,
            session_config.workspace.path.resolve(), self.path, self.is_write)
        return GrantPreview(resource_text=", ".join(str(p) for p in paths), block=True)

    def apply_grant(
        self, action: "GrantAction", scope: "GrantScope",
        session_config: "SessionConfig", process_config: "ProcessConfig | None",
        *, grant_patterns: list[list[str]] | None = None,
    ) -> None:
        from klorb.permissions.grant import apply_permission_grant
        apply_permission_grant(
            action, scope, session_config, process_config, self.path, self.is_write)

    def added_to_override(self, override: PermissionOverride) -> PermissionOverride:
        return PermissionOverride(
            paths=override.paths | {self.path}, commands=override.commands,
            reasons=override.reasons, skills=override.skills, domains=override.domains)


@dataclass(frozen=True)
class CommandResource(PermissionResource):
    """A bash-command-rule ask: `argv` is the exact token list a simple command was invoked
    with."""

    argv: tuple[str, ...]

    def header_kind(self) -> str:
        return "Run command"

    def preview_text(self) -> str | None:
        return None

    def grant_preview(self, session_config: "SessionConfig") -> GrantPreview:
        from klorb.permissions.command_grant import compute_command_grant_patterns
        patterns = compute_command_grant_patterns(session_config.command_rules, list(self.argv))
        return GrantPreview(resource_text=", ".join(" ".join(pattern) for pattern in patterns))

    def apply_grant(
        self, action: "GrantAction", scope: "GrantScope",
        session_config: "SessionConfig", process_config: "ProcessConfig | None",
        *, grant_patterns: list[list[str]] | None = None,
    ) -> None:
        from klorb.permissions.command_grant import apply_command_permission_grant
        apply_command_permission_grant(
            action, scope, session_config, process_config, list(self.argv), grant_patterns)

    def added_to_override(self, override: PermissionOverride) -> PermissionOverride:
        return PermissionOverride(
            paths=override.paths, commands=override.commands | {self.argv},
            reasons=override.reasons, skills=override.skills, domains=override.domains)


@dataclass(frozen=True)
class SkillResource(PermissionResource):
    """A skill-activation ask: `skill_id` is the `(namespace, name)` pair being activated."""

    skill_id: tuple[str, str]

    def header_kind(self) -> str:
        return "Activate skill"

    def preview_text(self) -> str | None:
        namespace, name = self.skill_id
        return f"/{name} ({namespace})"

    def grant_preview(self, session_config: "SessionConfig") -> GrantPreview:
        return GrantPreview(resource_text=self.preview_text() or "")

    def apply_grant(
        self, action: "GrantAction", scope: "GrantScope",
        session_config: "SessionConfig", process_config: "ProcessConfig | None",
        *, grant_patterns: list[list[str]] | None = None,
    ) -> None:
        from klorb.permissions.skill_grant import apply_skill_permission_grant
        apply_skill_permission_grant(action, scope, session_config, process_config, self.skill_id)

    def added_to_override(self, override: PermissionOverride) -> PermissionOverride:
        return PermissionOverride(
            paths=override.paths, commands=override.commands, reasons=override.reasons,
            skills=override.skills | {self.skill_id}, domains=override.domains)


@dataclass(frozen=True)
class DomainResource(PermissionResource):
    """A `WebFetch` domain-access ask: `url` is the full URL a tool is trying to retrieve, shown
    verbatim to the user; `domain` (derived, not stored, since it's cheap to recompute and storing
    it separately would risk drifting out of sync with `url`) is what grant/override checks
    actually key on, matching `domain_grant.py`/`PermissionOverride.domains`, which are
    domain-keyed rather than URL-keyed."""

    url: str

    @property
    def domain(self) -> str:
        from klorb.permissions.domain_access import parse_domain
        return parse_domain(self.url)

    def header_kind(self) -> str:
        return "Fetch URL"

    def preview_text(self) -> str | None:
        return self.url

    def grant_preview(self, session_config: "SessionConfig") -> GrantPreview:
        return GrantPreview(resource_text=self.domain)

    def apply_grant(
        self, action: "GrantAction", scope: "GrantScope",
        session_config: "SessionConfig", process_config: "ProcessConfig | None",
        *, grant_patterns: list[list[str]] | None = None,
    ) -> None:
        from klorb.permissions.domain_grant import apply_domain_permission_grant
        apply_domain_permission_grant(action, scope, session_config, process_config, self.domain)

    def added_to_override(self, override: PermissionOverride) -> PermissionOverride:
        return PermissionOverride(
            paths=override.paths, commands=override.commands, reasons=override.reasons,
            skills=override.skills, domains=override.domains | {self.domain})


@dataclass(frozen=True)
class StructuralResource(PermissionResource):
    """A `BashTool` ask item the shell walker couldn't classify into a specific path or command
    rule: `reason` is the walker's own deterministic forced-ask explanation, doubling as this
    resource's override identity (see `PermissionOverride.reasons`). Has no persistable rule of
    its own -- only `"once"`/deny make sense for it, never a session/workspace/homedir grant."""

    reason: str

    def header_kind(self) -> str:
        return "Confirm"

    def preview_text(self) -> str | None:
        return None

    @property
    def is_persistable(self) -> bool:
        return False

    def grant_preview(self, session_config: "SessionConfig") -> GrantPreview | None:
        return None

    def apply_grant(
        self, action: "GrantAction", scope: "GrantScope",
        session_config: "SessionConfig", process_config: "ProcessConfig | None",
        *, grant_patterns: list[list[str]] | None = None,
    ) -> None:
        return None

    def added_to_override(self, override: PermissionOverride) -> PermissionOverride:
        return PermissionOverride(
            paths=override.paths, commands=override.commands,
            reasons=override.reasons | {self.reason}, skills=override.skills,
            domains=override.domains)
