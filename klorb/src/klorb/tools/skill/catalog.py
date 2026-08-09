# © Copyright 2026 Aaron Kimball
"""The session-scoped skill catalog: two `SkillCatalog`s built once per `Session`, from a single
disk scan across every tier, and reused by every subsequent skill lookup in that session instead
of re-walking the filesystem per call. See docs/specs/skills.md.

`SkillCatalogRegistry.canonical()` is keyed by every discovered skill's true `(namespace, name)`
identity -- its directory basename. This is the only catalog `ActivateSkill`/`ReadSkillFile` may
resolve against, and the only identity `skillRules` rules and approval decisions are ever keyed
on.

`SkillCatalogRegistry.typed()` additionally carries an alias entry `(namespace, <frontmatter
name>)` for a skill whose `SKILL.md` frontmatter names it something other than its directory
basename. This is the catalog a user's typed `/<name>` or `/<namespace>:<name>` reference is
checked against, via `SkillCatalog.resolve_reference()`, before the harness treats it as a real
skill mention.

Each `Session` owns exactly one `SkillCatalogRegistry` instance (`Session.skill_catalog_registry`,
built fresh in `SessionCoreMixin.__init__`) -- every catalog lookup goes through a method call on
that instance rather than a scattered set of module-level globals and free functions (see
AGENTS.md's "encapsulate singleton state in a class" principle). Both catalogs are built once,
lazily, on first use (`SkillCatalogRegistry.ensure()`); `SkillCatalogRegistry.reload()` forces a
fresh disk scan -- `Session.reload_skills()`'s implementation, itself the ">Reload skills" command
palette action's implementation (see `klorb.tui.commands.skill_commands`). A skill added, removed,
or edited on disk between then and now is invisible until the catalog is rebuilt; a brand-new
`Session` always starts from a fresh, empty registry, so it rescans the disk on its own first use
rather than inheriting whatever a previous session's catalog held.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from klorb.permissions.resource import PermissionOverride
from klorb.permissions.skill_access import VALID_NAMESPACES, SkillId, SkillRules, evaluate_skill, format_fqsn
from klorb.tools.skill.common import (
    display_skill_name,
    is_valid_skill_name,
    parse_frontmatter,
    raise_if_skill_not_allowed,
    read_skill_md,
    resolve_all_skills,
    validate_namespace,
    validate_skill_name,
)
from klorb.tools.skill.model import Skill

if TYPE_CHECKING:
    # Deferred: `klorb.tools.setup_context` imports `klorb.session`, which imports this module
    # (`Session._ensure_skill_catalog`) -- a runtime import here would cycle back.
    from klorb.tools.setup_context import ToolSetupContext

logger = logging.getLogger(__name__)


class SkillCatalog(BaseModel):
    """One `(namespace, name) -> Skill` dict, plus the read-only views/lookups over it. Two
    instances exist per `SkillCatalogRegistry` -- see module docstring -- with the same shape but
    different contents: `SkillCatalogRegistry.canonical()`'s `skills` holds only true identities,
    `SkillCatalogRegistry.typed()`'s also holds frontmatter-alias identities pointing at the same
    `Skill` objects.
    """

    model_config = ConfigDict(frozen=True)

    skills: dict[SkillId, Skill]

    def get(self, skill_id: SkillId) -> Skill | None:
        """The `Skill` at `skill_id`, or `None` if this catalog has no such identity."""
        return self.skills.get(skill_id)

    def __len__(self) -> int:
        return len(self.skills)

    def precedence_deduped(self) -> list[Skill]:
        """Every distinct skill `name` across all tiers, resolved to a single winning `Skill` by
        namespace precedence (`VALID_NAMESPACES` order: `user`, then `workspace`, then
        `internal`) -- the same all-or-nothing shadowing `resolve_prompt_file()` uses. Sorted by
        `name`. This is what the `AvailableSkills` interjection and `SearchSkills` enumerate: a
        lower-precedence tier's same-named skill is still resolvable directly via its exact
        `(namespace, name)` pair, just not listed as *the* meaning of that bare name.
        """
        rank: dict[str, int] = {namespace: index for index, namespace in enumerate(VALID_NAMESPACES)}
        winners: dict[str, Skill] = {}
        for (namespace, name), skill in self.skills.items():
            current = winners.get(name)
            if current is None or rank[namespace] < rank[current.namespace]:
                winners[name] = skill
        return sorted(winners.values(), key=lambda skill: skill.name)

    def discoverable(self, skill_rules: SkillRules) -> list[Skill]:
        """Every currently non-`deny`-verdicted skill, precedence-deduped by name -- what the
        `AvailableSkills` interjection lists and `SearchSkills` narrows. No disk access."""
        return [
            skill for skill in self.precedence_deduped()
            if evaluate_skill(skill_rules, (skill.namespace, skill.name)) != "deny"
        ]

    def resolve_reference(self, token: str) -> Skill | None:
        """Resolve a user-typed skill reference -- a bare name (`foo`) or a colon-qualified fully-
        qualified name (`namespace:foo`, see `klorb.permissions.skill_access.format_fqsn`).

        A colon-qualified token resolves only that exact `(namespace, name)` pair (which may be a
        canonical name or a frontmatter alias), or nothing at all -- it never falls back to a bare
        search. A bare name is checked across namespaces in precedence order (`user`, `workspace`,
        `internal`), returning the first tier with a match (canonical or alias)."""
        if ":" in token:
            namespace, _, name = token.partition(":")
            return self.skills.get((namespace, name))
        for namespace in VALID_NAMESPACES:
            skill = self.skills.get((namespace, token))
            if skill is not None:
                return skill
        return None


class SkillCatalogs(BaseModel):
    """The `(typed, canonical)` catalog pair `build_catalogs()` produces together from one disk
    scan -- a small named bundle rather than a positional tuple, since the two are always built
    and consumed as a pair (see `SkillCatalogRegistry.reload()`)."""

    model_config = ConfigDict(frozen=True)

    typed: SkillCatalog
    """
    Catalog mapping /skill-names that are  _typed_ by the user in a msg to skills. May include
    aliases if frontmatter name disagrees with canonical (`/dir` basename) name for the skill.
    """

    canonical: SkillCatalog
    """
    Catalog mapping canonical (`dir/` basename) /skill-names to Skills. Klorb agent is restricted
    to invoking skills as-identified in this list. Permission grants are reconciled only against the
    names in this catalog set.
    """


def build_catalogs(
    *, workspace_root: Path, workspace_trusted: bool, claude_skills_compat: bool,
    skill_rules: SkillRules,
) -> SkillCatalogs:
    """Scan every tier once and return the `(typed, canonical)` `SkillCatalog`s. Pure aside from
    reading `skill_rules` -- touches no shared state, so it's independently testable and is what
    `SkillCatalogRegistry.reload()` calls to actually populate a session's catalogs.

    A skill whose `(namespace, name)` verdict against `skill_rules` is already `"deny"` at scan
    time is excluded from both catalogs entirely -- it never becomes resolvable, listable, or
    even nameable by the model or a user's typed reference, since a `"deny"` verdict can never
    become anything else for that identity within this catalog's lifetime. This is a stronger
    exclusion than `SkillCatalog.discoverable()`'s runtime filter: a skill denied *after* the
    catalog was already built (e.g. via an interactive ask answered "deny" mid-session) stays
    resolvable in memory until an explicit reload, so its `skillRules` verdict is still checked
    at every use -- see docs/specs/skills.md.

    A skill whose frontmatter `name` disagrees with its directory basename logs a warning (it's
    still discoverable under its canonical basename; the frontmatter name is only ever added to
    `typed` as an alias, never used as the skill's identity) -- see docs/specs/skills.md. The
    canonical basename itself is lowercased and capped to `MAX_SKILL_NAME_DISPLAY_LENGTH` -- the
    identity both catalogs key on, and what's advertised to the model or a user-facing skill list,
    is always this lowercased, length-capped form, so a name once advertised always resolves. The
    full (untruncated) lowercased basename, and both the full and capped forms of a frontmatter
    alias, are added to `typed` as additional aliases when they differ from the canonical name --
    see `Skill.aliases`.
    """
    canonical: dict[SkillId, Skill] = {}
    typed: dict[SkillId, Skill] = {}
    for resolved in resolve_all_skills(
            workspace_root=workspace_root, workspace_trusted=workspace_trusted,
            claude_skills_compat=claude_skills_compat):
        full_name = resolved.name.lower()
        canonical_name = display_skill_name(full_name)
        skill_id: SkillId = (resolved.namespace, canonical_name)
        if evaluate_skill(skill_rules, skill_id) == "deny":
            logger.debug(
                "Skill %s excluded from catalog: skillRules verdict is deny", format_fqsn(skill_id))
            continue
        if skill_id in canonical:
            logger.warning(
                "Skill %s (%s) collides with another skill after lowercasing/truncation; the "
                "first one found wins, this one is dropped.", resolved.name, resolved.namespace)
            continue

        try:
            text = read_skill_md(resolved)
        except (OSError, UnicodeDecodeError):
            text = ""
        raw = parse_frontmatter(text)

        description = raw.get("description")
        if not isinstance(description, str):
            description = ""
        else:
            description = description.strip()

        disable_model_invocation = raw.get("disable-model-invocation") is True

        aliases = {full_name, canonical_name}
        frontmatter_name = raw.get("name")
        if isinstance(frontmatter_name, str) and frontmatter_name:
            if frontmatter_name != canonical_name:
                logger.warning(
                    "Skill %s (%s) frontmatter name %r disagrees with its directory basename "
                    "%r; the basename remains canonical, the frontmatter name is usable only as "
                    "an alias.", canonical_name, resolved.namespace, frontmatter_name, canonical_name)
            if is_valid_skill_name(frontmatter_name):
                aliases.add(frontmatter_name)
                aliases.add(display_skill_name(frontmatter_name))
            else:
                logger.warning(
                    "Skill %s (%s) frontmatter alias %r skipped: invalid name.",
                    canonical_name, resolved.namespace, frontmatter_name)

        skill = Skill(
            namespace=resolved.namespace, name=canonical_name, description=description,
            raw=raw, aliases=aliases, disable_model_invocation=disable_model_invocation,
            root=resolved.root)
        typed[skill_id] = skill
        if not disable_model_invocation:
            canonical[skill_id] = skill

    # Second pass: add alias entries, only once every skill's canonical identity is known, so an
    # alias can never shadow another skill's real (namespace, name) identity. Processed in
    # alphabetical (namespace, name) order, and checked against `typed` (not just `canonical`),
    # so two skills whose frontmatter names collide with each other -- not with any canonical
    # name -- resolve deterministically to the alphabetically-first skill, rather than to
    # whichever the filesystem happened to yield last.
    for namespace, canonical_name in sorted(canonical):
        skill = canonical[(namespace, canonical_name)]
        for alias in sorted(skill.aliases):
            if alias == canonical_name:
                continue
            alias_id: SkillId = (namespace, alias)
            if alias_id in typed:
                logger.warning(
                    "Skill %s:%s frontmatter alias %r collides with another skill's canonical "
                    "name or alias; the alias is dropped.", namespace, canonical_name, alias)
                continue
            typed[alias_id] = skill

    return SkillCatalogs(typed=SkillCatalog(skills=typed), canonical=SkillCatalog(skills=canonical))


def resolve_and_gate_skill(
    *, catalog: SkillCatalog, typed_catalog: SkillCatalog, skill_rules: SkillRules,
    override: PermissionOverride | None, namespace: object, name: object,
) -> Skill:
    """Validate `namespace`/`name`, resolve the pair against the session's canonical skill
    `catalog` (see `SkillCatalogRegistry.canonical()`), and enforce its `skillRules` verdict --
    the shared front half of `ActivateSkill` and `ReadSkillFile`. Raises `ValueError` for a malformed
    argument or an unknown pair, and `PermissionError`/`PermissionAskRequired` per
    `raise_if_skill_not_allowed`.

    `catalog` is always the *canonical* one, never the typed/alias one: `ActivateSkill`/
    `ReadSkillFile` only ever resolve a skill's true `(namespace, name)` identity, exactly as
    given, never through a frontmatter-name alias -- see docs/specs/skills.md.

    A `disable-model-invocation` skill is never in `catalog` (see `build_catalogs`), so it's
    unresolvable here by construction -- except `typed_catalog` is also consulted, purely to
    give a caller that guessed such a skill's name a specific `ValueError` explaining *why*
    (rather than the generic "no such skill", which reads the same as a typo), directing it at
    the one legitimate way in: the user's own message starting with `/<name>`.
    """
    validated_namespace = validate_namespace(namespace)
    validated_name = validate_skill_name(name)
    skill_id = (validated_namespace, validated_name)
    skill = catalog.get(skill_id)
    if skill is None:
        typed_skill = typed_catalog.get(skill_id)
        if typed_skill is not None and typed_skill.disable_model_invocation:
            raise ValueError(
                f"Skill {format_fqsn(skill_id)} cannot be loaded by name -- "
                "it only activates when the user's own message starts with "
                f"\"/{typed_skill.name}\". Tell the user to invoke it that way if they want to "
                "use it; do not retry this call.")
        raise ValueError(f"no such skill: {format_fqsn(skill_id)}")
    raise_if_skill_not_allowed(
        skill_rules, override, validated_namespace, validated_name,
        description=skill.description)
    return skill


class SkillCatalogRegistry:
    """Holds the two skill catalogs a `Session` owns and the logic to (re)build them. One
    instance exists per `Session` (`Session.skill_catalog_registry`); nothing outside this class
    reads or writes its state directly.
    """

    def __init__(self) -> None:
        self._typed: SkillCatalog | None = None
        self._canonical: SkillCatalog | None = None

    def ensure(
        self, *, workspace_root: Path, workspace_trusted: bool, claude_skills_compat: bool,
        skill_rules: SkillRules,
    ) -> None:
        """Build both catalogs if this registry hasn't built them yet. A no-op on every call
        after the first -- the tiers are scanned exactly once; reflecting a change to disk (or to
        workspace trust) requires an explicit `reload()` call."""
        if self._typed is not None:
            return
        self.reload(
            workspace_root=workspace_root, workspace_trusted=workspace_trusted,
            claude_skills_compat=claude_skills_compat, skill_rules=skill_rules)

    def ensure_from_context(self, context: "ToolSetupContext") -> None:
        """`ensure()`, extracting `workspace_root`/`workspace_trusted`/`claude_skills_compat`/
        `skill_rules` from a `Tool`'s `ToolSetupContext` -- the common case every skill
        `Tool.apply()` needs before touching the catalog."""
        workspace = context.session_config.workspace
        self.ensure(
            workspace_root=workspace.path, workspace_trusted=workspace.trusted,
            claude_skills_compat=context.process_config.compatibility_claude_skills,
            skill_rules=context.session_config.skill_rules)

    def reload(
        self, *, workspace_root: Path, workspace_trusted: bool, claude_skills_compat: bool,
        skill_rules: SkillRules,
    ) -> SkillCatalogs:
        """Rebuild both catalogs from a fresh disk scan, replacing whatever was held -- the
        ">Reload skills" command's implementation. Returns the new catalog pair."""
        logger.debug(
            "Reloading skill catalog: workspace_root=%r workspace_trusted=%r "
            "claude_skills_compat=%r", workspace_root, workspace_trusted, claude_skills_compat)
        catalogs = build_catalogs(
            workspace_root=workspace_root, workspace_trusted=workspace_trusted,
            claude_skills_compat=claude_skills_compat, skill_rules=skill_rules)
        self._typed, self._canonical = catalogs.typed, catalogs.canonical
        logger.info(
            "Skill catalog reloaded: %d skill(s), %d typed reference(s)",
            len(catalogs.canonical), len(catalogs.typed))
        return catalogs

    def typed(self) -> SkillCatalog:
        """The catalog a user's typed `/<name>` or `/<namespace>:<name>` reference is checked
        against -- includes frontmatter-alias identities. Raises `RuntimeError` if the catalog
        hasn't been built yet (call `ensure()` first)."""
        if self._typed is None:
            raise RuntimeError("skill catalog not yet initialized -- call ensure() first")
        return self._typed

    def canonical(self) -> SkillCatalog:
        """The catalog `ActivateSkill`/`ReadSkillFile` resolve against -- canonical identities
        only, no aliases. Raises `RuntimeError` if the catalog hasn't been built yet."""
        if self._canonical is None:
            raise RuntimeError("skill catalog not yet initialized -- call ensure() first")
        return self._canonical


def resolve_session_skill_catalog_registry(context: "ToolSetupContext") -> SkillCatalogRegistry:
    """Return `context.session`'s own `SkillCatalogRegistry`, ensuring its catalogs are built
    against `context`'s live workspace/trust/compat settings first -- the common first step every
    skill `Tool.apply()` needs. Raises `ValueError` if `context` wasn't built with a real `Session`
    (e.g. a `ToolSetupContext` constructed directly, as most unit tests for other tools do): skill
    catalogs are per-session state, so a skill `Tool` has nothing to resolve against without one.
    """
    if context.session is None:
        raise ValueError("skill tools require an active session")
    registry = context.session.skill_catalog_registry
    registry.ensure_from_context(context)
    return registry
