# © Copyright 2026 Aaron Kimball
"""Exceptions shared across the `klorb.tools` package.

Extracted into their own module so consumers (notably `klorb.session`, for
`_run_tool_calls`'s statistics tracking) can reference tool-package exceptions without
pulling in `klorb.tools.registry` — which itself imports `klorb.session.Session` at
module scope and would form a circular import otherwise. `ErrorCategory` and `ToolCallError`
live here rather than in `klorb.tools.response_envelope` for the same reason: this module
stays a dependency-light leaf every tool can import without pulling in pydantic-model
machinery, and `response_envelope.py` depends on this module in one direction only.
"""

from typing import Any, Literal

ErrorCategory = Literal["transient", "syntax", "validation", "permission", "business_logic"]
"""How a failed tool call should be treated by the model: `"transient"` (a network hiccup or
similar -- retrying might help), `"syntax"` (malformed call arguments -- fix and retry),
`"validation"` (a bad argument value -- fix and retry), `"permission"` (access was denied --
retrying won't help without a different approach), `"business_logic"` (the call ran but didn't
achieve its goal -- e.g. a shell command that exited non-zero). See
`klorb.tools.response_envelope` for how this feeds `ToolResponseEnvelope.is_retryable`."""


class NoSuchToolException(Exception):
    """Raised by `klorb.tools.registry.ToolRegistry.instantiate_tool` when no tool with the
    requested name was discovered during the module walk."""

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"No such tool: {tool_name!r}")


class ToolCallError(Exception):
    """Raise from any `Tool.apply()` to signal a categorized failure without inventing a
    tool-specific result-dict failure shape (see `klorb.tools.response_envelope`).
    `response_body`, if given, becomes the failed call's `ToolResponseEnvelope.response_body`
    instead of `None` -- for a tool whose failure carries data worth keeping (partial output,
    diagnostic detail) even though the call as a whole didn't succeed.
    """

    def __init__(
        self, message: str, *, category: ErrorCategory = "business_logic",
        response_body: Any = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.response_body = response_body
