# © Copyright 2026 Aaron Kimball
"""`ToolResponseEnvelope`: the structured JSON object every `role="tool_response"`
`Message.content` is serialized from, giving the model a uniform set of fields
(`is_error`/`is_retryable`/`error_category`/`error_message`) and a `system_interjections` slot
for harness advisories. See docs/specs/session-and-turns.md.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from klorb.tools.exceptions import ErrorCategory, NoSuchToolException, ToolCallError

_RETRYABLE_CATEGORIES: frozenset[ErrorCategory] = frozenset({"transient", "syntax", "validation"})


class SystemInterjectionPayload(BaseModel):
    """One standing-interjection provider's advisory, attached to a `ToolResponseEnvelope`'s
    `system_interjections`. `subject` is the provider's registration key; `body` is the message
    it returned."""

    model_config = ConfigDict(frozen=True)

    subject: str
    body: str


class ToolResponseEnvelope(BaseModel):
    """The JSON object every `tool_response` `Message.content` is serialized from (via
    `to_wire_dict()`). Construct via `success()`/`error()` so `is_retryable` always stays
    derived from `error_category`."""

    model_config = ConfigDict(frozen=True)

    is_error: bool
    is_retryable: bool
    error_category: ErrorCategory | None = None
    error_message: str | None = None
    response_body: Any = None
    system_interjections: list[SystemInterjectionPayload] = Field(default_factory=list)

    @classmethod
    def success(
        cls,
        response_body: Any,
        *,
        system_interjections: "tuple[SystemInterjectionPayload, ...]" = (),
    ) -> "ToolResponseEnvelope":
        """Build a successful call's envelope. `is_retryable` is unconditionally `False`."""
        return cls(
            is_error=False, is_retryable=False, response_body=response_body,
            system_interjections=list(system_interjections))

    @classmethod
    def error(
        cls,
        message: str | None,
        *,
        category: ErrorCategory | None,
        response_body: Any = None,
        system_interjections: "tuple[SystemInterjectionPayload, ...]" = (),
    ) -> "ToolResponseEnvelope":
        """Build a failed call's envelope. `is_retryable` is derived from `category` via
        `_RETRYABLE_CATEGORIES`, `False` when `category` is `None`. `message` is `None` when
        failure detail already lives inside `response_body`."""
        return cls(
            is_error=True, is_retryable=category in _RETRYABLE_CATEGORIES,
            error_category=category, error_message=message, response_body=response_body,
            system_interjections=list(system_interjections))

    def to_wire_dict(self) -> dict[str, Any]:
        """Serialize to the dict that becomes a `tool_response` `Message.content`'s JSON body:
        `model_dump(exclude_none=True)`, additionally dropping `system_interjections` entirely
        when empty."""
        wire = self.model_dump(exclude_none=True)
        if not self.system_interjections:
            wire.pop("system_interjections", None)
        return wire


def classify_exception(exc: Exception) -> tuple[str, ErrorCategory | None, Any]:
    """Return `(message, category, response_body)` for a tool-call exception so categorization
    is never duplicated or missed on a retry path.

    `PermissionError` classification covers both `klorb.permissions.table.
    raise_if_not_allowed`'s explicit `raise PermissionError(...)` for a `"deny"` verdict, and a
    real OS-level access-denied failure -- Python already maps `errno=EACCES` and Windows
    `winerror=5` uniformly onto the builtin `PermissionError` (a subclass of `OSError`), so no
    platform-specific errno/winerror inspection is needed here.

    `NoSuchToolException` is folded into the same `"validation"` branch as `ValueError` for a
    retried call that hits it; `_run_tool_calls`'s primary (first-attempt) dispatch keeps its own
    dedicated `except NoSuchToolException` branch instead of routing through this function.
    """
    if isinstance(exc, ToolCallError):
        return str(exc), exc.category, exc.response_body
    if isinstance(exc, PermissionError):
        return str(exc), "permission", None
    if isinstance(exc, (ValueError, NoSuchToolException)):
        return str(exc), "validation", None
    return str(exc), None, None
