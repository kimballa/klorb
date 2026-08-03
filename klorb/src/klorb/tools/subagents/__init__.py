# © Copyright 2026 Aaron Kimball
"""CreateSubagent/WaitForSubagent/MessageSubagent: the tools an agent uses to launch and
converse with a specialist subagent session -- see docs/specs/subagents.md and
`klorb.agents.policy`/`klorb.agents.runtime` for the orchestration these tools drive.

Deliberately does not import any of this subpackage's `Tool` subclasses here: `ToolRegistry`
discovers them itself by walking this subpackage's modules directly (see
`klorb.tools.registry.ToolRegistry.discover_tools`), and importing them into this
`__init__.py` would only reintroduce the import cycle `klorb.tools.setup_context` importing
`klorb.session` for real is designed to avoid.
"""
