# © Copyright 2026 Aaron Kimball
"""Exceptions shared across the `klorb.tools` package."""

from typing import Any, Literal

ErrorCategory = Literal[
    "transient", "syntax", "validation", "permission", "business_logic", "signaled"]
"""How a failed tool call should be treated by the model: `"transient"` (a network hiccup or
similar -- retrying might help), `"syntax"` (malformed call arguments -- fix and retry),
`"validation"` (a bad argument value -- fix and retry), `"permission"` (access was denied --
retrying won't help without a different approach), `"business_logic"` (the call ran but didn't
achieve its goal), `"signaled"` (the call was interrupted by an external signal -- not
retryable)."""


class NoSuchToolException(Exception):
    """Raised by `klorb.tools.registry.ToolRegistry.instantiate_tool` when no tool with the
    requested name was discovered during the module walk."""

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"No such tool: {tool_name!r}")


class ToolCallError(Exception):
    """Raise from any `Tool.apply()` to signal a categorized failure without inventing a
    tool-specific result-dict failure shape. `response_body`, if given, becomes the failed
    call's `ToolResponseEnvelope.response_body` instead of `None`."""

    def __init__(
        self, message: str, *, category: ErrorCategory = "business_logic",
        response_body: Any = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.response_body = response_body


class ToolInterruptError(ToolCallError):
    """Raised from a `Tool.apply()` when the tool is interrupted by an external signal:
    the user cancelling (`reason="user_cancel"`), the user sending a new message while
    the tool is blocked (`reason="new_message"`), or a caller-specified timeout expiring
    (`reason="timeout"`). `response_body` is always set to an `{"incomplete": True,
    "incomplete_reason": <reason>, ...}` dict so the model sees a uniform shape regardless
    of which interrupt triggered it.
    """

    def __init__(
        self,
        reason: Literal["user_cancel", "new_message", "timeout"],
        *,
        details: str | None = None,
    ) -> None:
        category: ErrorCategory = "transient" if reason == "timeout" else "signaled"
        response_body: dict[str, Any] = {"incomplete": True, "incomplete_reason": reason}
        if details is not None:
            response_body["details"] = details
        if reason == "user_cancel":
            message = "Wait canceled: the user interrupted the tool."
        elif reason == "new_message":
            message = "Wait canceled: the user sent a new message while waiting."
        else:
            message = details or "Tool interrupted by timeout."
        super().__init__(message, category=category, response_body=response_body)
