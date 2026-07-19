# © Copyright 2026 Aaron Kimball
"""The process-wide skill catalog: two `SkillCatalog`s built once, from a single disk scan across
every tier, and reused by every subsequent skill lookup instead of re-walking the filesystem per
call. See docs/specs/skills.md.

`canonical_catalog()` is keyed by every discovered skill's true `(namespace, name)` identity --
its directory basename. This is the only catalog `ActivateSkill`/`ReadSkillFile` may resolve
against, and the only identity `skillRules` rules and approval decisions are ever keyed on.

`typed_catalog()` additionally carries an alias entry `(namespace, <frontmatter name>)` for a
skill whose `SKILL.md` frontmatter names it something other than its directory basename. This is
the catalog a user's typed `/<name>` or `/<namespace>:<name>` reference is checked against, via
`SkillCatalog.resolve_reference()`, before the harness treats it as a real skill mention.

Both catalogs are built once, lazily, on first use (`ensure_skill_catalog()`);
`reload_skill_catalog()` forces a fresh disk scan -- the ">Reload skills" command palette action's
implementation (see `klorb.tui.commands.skill_commands`). A skill added, removed, or edited on
disk between then and now is invisible until the catalog is rebuilt.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from klorb.permissions.skill_access import VALID_NAMESPACES, SkillId, SkillRules, evaluate_skill, format_fqsn
from klorb.permissions.table import PermissionOverride
from klorb.tools.skill.common import (
    is_valid_skill_name,
    parse_frontmatter,
    raise_if_skill_not_allowed,
    read_skill_md,
    resolve_all_skills,
    validate_namespace,
    validate_skill_name,
)
from klorb.tools.skill.model import Skill

logger = logging.getLogger(__name__)


class SkillCatalog(BaseModel):
    """One `(namespace, name) -> Skill` dict, plus the read-only views/lookups over it. Two
    instances exist process-wide -- see module docstring -- with the same shape but different
    contents: `canonical_catalog()`'s `skills` holds only true identities, `typed_catalog()`'s
    also holds frontmatter-alias identities pointing at the same `Skill` objects.
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


@dataclass(frozen=True)
class SkillCatalogParams:
    """The disk-scan parameters the currently-loaded catalog was last built from, kept only so
    `ensure_skill_catalog()` can log what it's about to (not) rebuild."""

    workspace_root: Path
    workspace_trusted: bool
    claude_skills_compat: bool


def build_catalogs(
    *, workspace_root: Path, workspace_trusted: bool, claude_skills_compat: bool,
) -> tuple[SkillCatalog, SkillCatalog]:
    """Scan every tier once and return `(typed, canonical)` `SkillCatalog`s. Pure -- touches no
    module state, so it's independently testable and is what both `ensure_skill_catalog()`/
    `reload_skill_catalog()` call to actually populate the process-wide catalogs.

    A skill whose frontmatter `name` disagrees with its directory basename logs a warning (it's
    still discoverable under its canonical basename; the frontmatter name is only ever added to
    `typed` as an alias, never used as the skill's identity) -- see docs/specs/skills.md.
    """
    canonical: dict[SkillId, Skill] = {}
    typed: dict[SkillId, Skill] = {}
    for resolved in resolve_all_skills(
            workspace_root=workspace_root, workspace_trusted=workspace_trusted,
            claude_skills_compat=claude_skills_compat):
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

        aliases = {resolved.name}
        frontmatter_name = raw.get("name")
        if isinstance(frontmatter_name, str) and frontmatter_name:
            if frontmatter_name != resolved.name:
                logger.warning(
                    "Skill %s (%s) frontmatter name %r disagrees with its directory basename "
                    "%r; the basename remains canonical, the frontmatter name is usable only as "
                    "an alias.", resolved.name, resolved.namespace, frontmatter_name, resolved.name)
            if is_valid_skill_name(frontmatter_name):
                aliases.add(frontmatter_name)

        skill = Skill(
            namespace=resolved.namespace, name=resolved.name, description=description,
            raw=raw, aliases=aliases, root=resolved.root)
        skill_id: SkillId = (resolved.namespace, resolved.name)
        canonical[skill_id] = skill
        typed[skill_id] = skill

    # Second pass: add alias entries, only once every skill's canonical identity is known, so an
    # alias can never shadow another skill's real (namespace, name) identity.
    for (namespace, canonical_name), skill in canonical.items():
        for alias in skill.aliases:
            if alias == canonical_name:
                continue
            alias_id: SkillId = (namespace, alias)
            if alias_id in canonical:
                logger.warning(
                    "Skill %s:%s frontmatter alias %r collides with another skill's canonical "
                    "name; the alias is dropped.", namespace, canonical_name, alias)
                continue
            typed[alias_id] = skill

    return SkillCatalog(skills=typed), SkillCatalog(skills=canonical)


def resolve_and_gate_skill(
    *, catalog: SkillCatalog, skill_rules: SkillRules,
    override: PermissionOverride | None, namespace: object, name: object,
) -> Skill:
    """Validate `namespace`/`name`, resolve the pair against the process-wide canonical skill
    `catalog` (see `canonical_catalog()`), and enforce its `skillRules` verdict -- the shared
    front half of `ActivateSkill` and `ReadSkillFile`. Raises `ValueError` for a malformed
    argument or an unknown pair, and `PermissionError`/`PermissionAskRequired` per
    `raise_if_skill_not_allowed`.

    `catalog` is always the *canonical* one, never the typed/alias one: `ActivateSkill`/
    `ReadSkillFile` only ever resolve a skill's true `(namespace, name)` identity, exactly as
    given, never through a frontmatter-name alias -- see docs/specs/skills.md.
    """
    validated_namespace = validate_namespace(namespace)
    validated_name = validate_skill_name(name)
    skill = catalog.get((validated_namespace, validated_name))
    if skill is None:
        raise ValueError(f"no such skill: {format_fqsn((validated_namespace, validated_name))}")
    raise_if_skill_not_allowed(
        skill_rules, override, validated_namespace, validated_name,
        description=skill.description)
    return skill


_typed_catalog: SkillCatalog | None = None
_canonical_catalog: SkillCatalog | None = None
_catalog_params: SkillCatalogParams | None = None


def ensure_skill_catalog(
    *, workspace_root: Path, workspace_trusted: bool, claude_skills_compat: bool,
) -> None:
    """Build the process-wide catalog if it hasn't been built yet in this process. A no-op on
    every call after the first -- the tiers are scanned exactly once; reflecting a change to disk
    (or to workspace trust) requires an explicit `reload_skill_catalog()` call."""
    if _typed_catalog is not None:
        return
    reload_skill_catalog(
        workspace_root=workspace_root, workspace_trusted=workspace_trusted,
        claude_skills_compat=claude_skills_compat)


def reload_skill_catalog(
    *, workspace_root: Path, workspace_trusted: bool, claude_skills_compat: bool,
) -> tuple[SkillCatalog, SkillCatalog]:
    """Rebuild the process-wide catalog from a fresh disk scan, replacing whatever was there --
    the ">Reload skills" command's implementation. Returns the new `(typed, canonical)` catalogs."""
    global _typed_catalog, _canonical_catalog, _catalog_params
    params = SkillCatalogParams(
        workspace_root=workspace_root, workspace_trusted=workspace_trusted,
        claude_skills_compat=claude_skills_compat)
    logger.debug("Reloading skill catalog: %r", params)
    typed, canonical = build_catalogs(
        workspace_root=workspace_root, workspace_trusted=workspace_trusted,
        claude_skills_compat=claude_skills_compat)
    _typed_catalog, _canonical_catalog, _catalog_params = typed, canonical, params
    logger.info(
        "Skill catalog reloaded: %d skill(s), %d typed reference(s)", len(canonical), len(typed))
    return typed, canonical


def typed_catalog() -> SkillCatalog:
    """The catalog a user's typed `/<name>` or `/<namespace>:<name>` reference is checked against
    -- includes frontmatter-alias identities. Raises `RuntimeError` if the catalog hasn't been
    built yet in this process (call `ensure_skill_catalog()` first)."""
    if _typed_catalog is None:
        raise RuntimeError("skill catalog not yet initialized -- call ensure_skill_catalog() first")
    return _typed_catalog


def canonical_catalog() -> SkillCatalog:
    """The catalog `ActivateSkill`/`ReadSkillFile` resolve against -- canonical identities only,
    no aliases. Raises `RuntimeError` if the catalog hasn't been built yet in this process."""
    if _canonical_catalog is None:
        raise RuntimeError("skill catalog not yet initialized -- call ensure_skill_catalog() first")
    return _canonical_catalog


def reset_skill_catalog_for_tests() -> None:
    """Clear the process-wide catalog so the next `ensure_skill_catalog()` call rebuilds it from
    scratch. Test-only seam: production code has no legitimate reason to un-build the catalog
    short of `reload_skill_catalog()`, which replaces it directly."""
    global _typed_catalog, _canonical_catalog, _catalog_params
    _typed_catalog = None
    _canonical_catalog = None
    _catalog_params = None
