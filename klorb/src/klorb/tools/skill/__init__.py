# © Copyright 2026 Aaron Kimball
"""SearchSkills/ActivateSkill/ReadSkillFile: the tools a model uses to narrow, load, and read the
supporting files of a skill -- a directory of reusable instructions for one bounded task. See
`klorb.tools.skill.common` and docs/specs/skills.md.

Deliberately does not import any of this subpackage's `Tool` subclasses here, for the same reason
`klorb.tools.scratchpad`/`klorb.tools.memory` don't: `ToolRegistry` discovers them itself by
walking this subpackage's modules directly (see `klorb.tools.registry.ToolRegistry._discover_tools`),
and importing them into this `__init__.py` would only reintroduce the import cycle that pattern is
designed to avoid (each tool module imports `klorb.tools.setup_context`, which imports
`klorb.session` for real). `klorb.tools.skill.common` itself is cycle-free -- it imports neither
`klorb.tools.setup_context` nor `klorb.session` -- so `klorb.session` imports it directly to build
its available-skills interjection.
"""
