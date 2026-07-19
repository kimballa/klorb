# © Copyright 2026 Aaron Kimball
"""EscalatePrivileges: the tool a model uses to request elevated privileges for workspace access.

Deliberately does not import this subpackage's `Tool` subclass here, for the same reason
`klorb.tools.ask`'s own `__init__.py` doesn't (see its docstring): `ToolRegistry`
discovers it itself by walking this subpackage's modules directly (see
`klorb.tools.registry.ToolRegistry.discover_tools`), and importing it into this `__init__.py`
would only reintroduce the import cycle that pattern is designed to avoid (`klorb.tools.
escalate_privileges.escalate_privileges` imports `klorb.tools.setup_context`, which imports
`klorb.session` for real).
"""
