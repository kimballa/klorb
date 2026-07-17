# © Copyright 2026 Aaron Kimball
"""SearchSkills/ActivateSkill/ReadSkillFile: the tools a model uses to narrow, load, and read the
supporting files of a skill. See `klorb.tools.skill.common` and docs/specs/skills.md.

Does not import its `Tool` subclasses here, like `klorb.tools.scratchpad`/`klorb.tools.memory`:
`ToolRegistry` discovers them by walking the subpackage, and importing them here would reintroduce
the `klorb.session` cycle.
"""
