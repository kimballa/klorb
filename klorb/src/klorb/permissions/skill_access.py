# © Copyright 2026 Aaron Kimball
"""Skill access control: a `PermissionsTable` resource kind governing which skills the
`ActivateSkill`/`ReadSkillFile` tools may load into the model's context, matched by exact
`(namespace, name)` identity. See docs/specs/skills.md and docs/specs/permissions.md.

A skill's identity is the `(namespace, name)` pair -- `namespace` one of `"workspace"`,
`"user"`, `"internal"` (the three discovery tiers), `name` the skill directory's bare-slug
basename. Keying on the full pair, not a bare name, is deliberate: a grant for
`("internal", "create-edit-skill")` can never be inherited by a same-named
`("workspace", "create-edit-skill")` skill a repository ships to shadow it -- the shadowing
skill is a distinct resource that must earn its own verdict.

Like `command_access`, this module has no dependency on `klorb.session`: `SessionConfig` holds a
`SkillRules` field, so importing `klorb.session` here would be circular.
"""

from typing import Literal

from pydantic import BaseModel, Field

from klorb.permissions.table import PermissionsTable, Verdict

Namespace = Literal["workspace", "user", "internal"]
"""The three skill discovery tiers, in most- to least-specific precedence order. Also the
`namespace` token every `(namespace, name)` skill-rule pair, and every skill tool's `namespace`
argument, names a skill's tier by."""

VALID_NAMESPACES: tuple[Namespace, ...] = ("workspace", "user", "internal")
"""Every valid `Namespace` literal, for validating a model- or config-supplied namespace string
(see `klorb.tools.skill.common.validate_namespace`)."""

SkillId = tuple[str, str]
"""A skill's `(namespace, name)` identity -- the candidate type `SkillsAccessTable` matches."""


class SkillRules(BaseModel):
    """One `skillRules` config key's `deny`/`ask`/`allow` rule lists, as plain `(namespace, name)`
    pairs -- see `SkillsAccessTable` for the evaluation logic that consumes this. On disk each
    entry is a two-element `["<namespace>", "<name>"]` array (pydantic coerces it to a tuple).
    Concatenated across config layers like `commandRules` (see
    `klorb.process_config.load_process_config`). Treated as immutable after construction,
    mirroring `CommandRules`' own documented contract: nothing in this codebase mutates these
    lists in place post-construction.
    """

    deny: list[SkillId] = Field(default_factory=list)
    ask: list[SkillId] = Field(default_factory=list)
    allow: list[SkillId] = Field(default_factory=list)


class SkillsAccessTable(PermissionsTable[SkillId]):
    """A `PermissionsTable` over `(namespace, name)` skill identities, matched by exact tuple
    equality only -- like `FileAccessTable`'s exact-path equality, not `DirectoryAccessTable`'s
    containment. A candidate matching no rule in any list evaluates to `None` from `evaluate()`,
    which `normalize_skill_verdict` folds to `"ask"`: a skill never activates merely because
    nothing explicitly denied it.
    """

    def __init__(self, rules: SkillRules) -> None:
        super().__init__(deny=list(rules.deny), ask=list(rules.ask), allow=list(rules.allow))

    def _matches(self, rule: SkillId, candidate: SkillId) -> bool:
        return rule == candidate


def normalize_skill_verdict(verdict: Verdict | None) -> Verdict:
    """Fold `SkillsAccessTable.evaluate()`'s `None` (no matching rule in any list) to `"ask"` --
    the same "no permissive default" fallback `CommandAccessTable` applies, so a skill is never
    loaded into context merely because nothing explicitly denied it."""
    return verdict if verdict is not None else "ask"


def evaluate_skill(skill_rules: SkillRules, skill_id: SkillId) -> Verdict:
    """Return the normalized `deny`/`ask`/`allow` verdict for `skill_id` against `skill_rules` --
    the single seam every caller (the available-skills interjection, `SearchSkills`,
    `ActivateSkill`, `ReadSkillFile`) evaluates a skill's verdict through, so they can never
    disagree on what counts as denied."""
    return normalize_skill_verdict(SkillsAccessTable(skill_rules).evaluate(skill_id))
