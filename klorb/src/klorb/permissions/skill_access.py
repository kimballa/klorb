# © Copyright 2026 Aaron Kimball
"""Skill access control: a `PermissionsTable` resource kind governing which skills the
`ActivateSkill`/`ReadSkillFile` tools may load into the model's context, keyed on the exact
`(namespace, name)` identity. See docs/specs/skills.md and docs/specs/permissions.md.
"""

from typing import Literal

from pydantic import BaseModel, Field

from klorb.permissions.table import PermissionsTable, Verdict

Namespace = Literal["workspace", "user", "internal"]
"""The three skill discovery tiers, in most- to least-specific precedence order."""

VALID_NAMESPACES: tuple[Namespace, ...] = ("workspace", "user", "internal")

SkillId = tuple[str, str]
"""A skill's `(namespace, name)` identity -- the candidate type `SkillsAccessTable` matches."""


class SkillRules(BaseModel):
    """One `skillRules` config key's `deny`/`ask`/`allow` rule lists of `(namespace, name)` pairs.
    On disk each entry is a two-element `["<namespace>", "<name>"]` array (pydantic coerces it to a
    tuple). Immutable after construction, like `CommandRules`."""

    deny: list[SkillId] = Field(default_factory=list)
    ask: list[SkillId] = Field(default_factory=list)
    allow: list[SkillId] = Field(default_factory=list)


class SkillsAccessTable(PermissionsTable[SkillId]):
    """A `PermissionsTable` over `(namespace, name)` skill identities, matched by exact tuple
    equality. A candidate matching no rule evaluates to `None`, which `normalize_skill_verdict`
    folds to `"ask"`."""

    def __init__(self, rules: SkillRules) -> None:
        super().__init__(deny=list(rules.deny), ask=list(rules.ask), allow=list(rules.allow))

    def _matches(self, rule: SkillId, candidate: SkillId) -> bool:
        return rule == candidate


def normalize_skill_verdict(verdict: Verdict | None) -> Verdict:
    """Fold `SkillsAccessTable.evaluate()`'s `None` (no matching rule) to `"ask"`, so a skill never
    activates merely because nothing denied it."""
    return verdict if verdict is not None else "ask"


def evaluate_skill(skill_rules: SkillRules, skill_id: SkillId) -> Verdict:
    """Return the normalized `deny`/`ask`/`allow` verdict for `skill_id` against `skill_rules`."""
    return normalize_skill_verdict(SkillsAccessTable(skill_rules).evaluate(skill_id))
