# © Copyright 2026 Aaron Kimball
"""ListMemories/SearchMemories/ReadMemory/EditMemory/CreateMemory/DeleteMemory: the tools a
model uses to record and recall durable notes about a workspace or the broader user/homedir
environment across sessions — see `klorb.tools.memory.common` and docs/specs/memories.md.

Deliberately does not import any of this subpackage's `Tool` subclasses here, for the same
reason `klorb.tools.scratchpad`'s own `__init__.py` doesn't (see its docstring):
`ToolRegistry` discovers them itself by walking this subpackage's modules directly (see
`klorb.tools.registry.ToolRegistry._discover_tools`), and importing them into this
`__init__.py` would only reintroduce the import cycle that pattern is designed to avoid
(this subpackage's tool modules import `klorb.tools.setup_context`, which imports
`klorb.session` for real).
"""
