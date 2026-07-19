# © Copyright 2026 Aaron Kimball
"""The `Skill` catalog record built once by `klorb.tools.skill.catalog` from a disk scan. See
docs/specs/skills.md.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict

from klorb.permissions.skill_access import Namespace


class Skill(BaseModel):
    """One discovered skill.

    `namespace` and `name` are the skill's canonical `(namespace, name)` identity -- `name` is
    always the skill directory's basename, never a frontmatter-supplied name, so it's what every
    `skillRules` rule and approval decision is keyed on. `description` is propagated straight
    from `raw["description"]` (empty string if absent or non-string). `raw` is the skill's full
    parsed YAML frontmatter, whatever attributes its author wrote. `aliases` is every string a
    user may type to mean this skill: the canonical basename, plus the frontmatter `name` when
    present, valid, and different from the basename.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    namespace: Namespace
    name: str
    "Canonical name for the skill, based on the dirname it lives in."
    description: str
    "Description from SKILL.md frontmatter."
    raw: dict[str, Any]
    "All SKILL.md frontmatter dict items"
    aliases: set[str]
    "Other names for the skill; e.g. if frontmatter name disagrees with the dirname."
    root: Any
    """The skill directory's `Traversable` root -- a real `Path` for the `workspace`/`user`
    tiers, or an `importlib.resources` `Traversable` for a zip-installed `internal` tier. `Any`
    rather than `Traversable` because pydantic can't validate an `importlib.resources` protocol
    type; `arbitrary_types_allowed` alone isn't enough since `Traversable` itself is a `Protocol`."""
