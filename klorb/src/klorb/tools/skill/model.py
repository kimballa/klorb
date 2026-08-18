# © Copyright 2026 Aaron Kimball
"""The `Skill` catalog record. See docs/specs/skills.md."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from klorb.permissions.skill_access import Namespace


class Skill(BaseModel):
    """One discovered skill.

    `namespace` and `name` are the skill's canonical `(namespace, name)` identity — `name` is
    the skill directory's basename, lowercased and capped to `MAX_SKILL_NAME_DISPLAY_LENGTH`,
    never a frontmatter-supplied name. `description` is propagated straight from
    `raw["description"]` (empty string if absent or non-string). `raw` is the skill's full parsed
    YAML frontmatter. `aliases` is every string a user may type to mean this skill: the full
    (untruncated) lowercased basename, the canonical (capped) basename, and the frontmatter `name`
    in both its full and capped forms, when present and valid.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    namespace: Namespace
    name: str
    "Canonical name for the skill: the dirname it lives in, lowercased and length-capped."
    description: str
    "Description from SKILL.md frontmatter."
    raw: dict[str, Any]
    "All SKILL.md frontmatter dict items"
    aliases: set[str]
    "Other names for the skill; e.g. its full untruncated basename, or a frontmatter alias."
    disable_model_invocation: bool = False
    """From frontmatter `disable-model-invocation: true`. Such a skill is never added to the
    canonical catalog `ActivateSkill`/`ReadSkillFile` resolve against, only to the typed one a
    user's own `/name` reference resolves against."""
    root: Any
    """The skill directory's `Traversable` root. `Any` rather than `Traversable` because pydantic
    can't validate an `importlib.resources` protocol type."""
