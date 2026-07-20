# © Copyright 2026 Aaron Kimball
"""TodoList/TodoNext/TodoCreate/TodoUpdate: the tools a model uses to track its work as
`chainlink` issues, scoped to the current session's label — see `klorb.tools.tasks.common` and
docs/specs/chainlink-task-tracking.md.

Deliberately does not import any of this subpackage's `Tool` subclasses here, for the same
reason `klorb.tools.memory`'s own `__init__.py` doesn't (see its docstring): `ToolRegistry`
discovers them itself by walking this subpackage's modules directly (see
`klorb.tools.registry.ToolRegistry.discover_tools`), and importing them into this `__init__.py`
would only reintroduce the import cycle that pattern is designed to avoid (this subpackage's
tool modules import `klorb.tools.setup_context`, which imports `klorb.session` for real).
"""
