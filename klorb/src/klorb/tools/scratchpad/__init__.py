# © Copyright 2026 Aaron Kimball
"""ReadScratchpad/EditScratchpad/SearchScratchpad: the tools a model uses to read, edit, and
search the active session's scratchpad file — see `klorb.tools.scratchpad.common.Scratchpad`
and docs/specs/scratchpad.md.

Deliberately does not import any of this subpackage's `Tool` subclasses here: `ToolRegistry`
discovers them itself by walking this subpackage's modules directly (see
`klorb.tools.registry.ToolRegistry.discover_tools`), and importing them into this
`__init__.py` would only reintroduce the very import cycle `klorb.session` importing
`klorb.tools.scratchpad.common.Scratchpad` is designed to avoid (`klorb.tools.scratchpad.read`
et al. import `klorb.tools.setup_context`, which imports `klorb.session` for real).
"""
