# © Copyright 2026 Aaron Kimball
"""Exceptions shared across the `klorb.tools` package.

Extracted into their own module so consumers (notably `klorb.session`, for
`_run_tool_calls`'s statistics tracking) can reference tool-package exceptions without
pulling in `klorb.tools.registry` — which itself imports `klorb.session.Session` at
module scope and would form a circular import otherwise.
"""


class NoSuchToolException(Exception):
    """Raised by `klorb.tools.registry.ToolRegistry.instantiate_tool` when no tool with the
    requested name was discovered during the module walk."""

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"No such tool: {tool_name!r}")
