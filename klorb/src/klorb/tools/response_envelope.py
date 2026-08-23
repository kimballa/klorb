# © Copyright 2026 Aaron Kimball
"""`ToolResponseEnvelope`: the structured JSON object every `role="tool_response"`
`Message.content` is persisted as, giving the model a uniform set of fields
(`is_error`/`is_retryable`/`error_category`/`error_message`) and a `system_interjections` slot
for harness advisories. See docs/specs/session-and-turns.md.
"""

import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from klorb.tools.exceptions import ErrorCategory, NoSuchToolException, ToolCallError

if TYPE_CHECKING:
    # Deferred: Tool is only ever used here as a type annotation -- to_wire_content() calls
    # format_response() on an already-constructed instance, never constructs one itself -- so
    # there's no need for this leaf module to carry klorb.tools.tool as a runtime dependency.
    from klorb.tools.tool import Tool

_RETRYABLE_CATEGORIES: frozenset[ErrorCategory] = frozenset({"transient", "syntax", "validation"})


def wrap_system_interjection(subject: str, message: str) -> str:
    """Wrap `message` in a `<SystemInterjection subject="{subject}">...</SystemInterjection>`
    tag pair, so a model can tell an out-of-band harness notice apart from the surrounding
    `Message.content`."""
    return f'<SystemInterjection subject="{subject}">\n{message}\n</SystemInterjection>'


def _format_bool(value: bool) -> str:
    """Render a bool as a lowercase `true`/`false` header-line value."""
    return "true" if value else "false"


class SystemInterjectionPayload(BaseModel):
    """One standing-interjection provider's advisory, attached to a `ToolResponseEnvelope`'s
    `system_interjections`. `subject` is the provider's registration key; `body` is the message
    it returned."""

    model_config = ConfigDict(frozen=True)

    subject: str
    body: str


class ToolCallErrorInfo(BaseModel):
    """`ToolResponseEnvelope`'s error reporting fields."""

    model_config = ConfigDict(frozen=True)

    is_error: bool
    is_retryable: bool
    error_category: ErrorCategory | None = None
    error_message: str | None = None


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
        """Serialize to the dict that becomes a `tool_response` `Message.content`'s persisted
        JSON body: `model_dump(exclude_none=True)`, additionally dropping `system_interjections`
        entirely when empty."""
        wire = self.model_dump(exclude_none=True)
        if not self.system_interjections:
            wire.pop("system_interjections", None)
        return wire

    def error_info(self) -> ToolCallErrorInfo:
        return ToolCallErrorInfo(
            is_error=self.is_error, is_retryable=self.is_retryable,
            error_category=self.error_category, error_message=self.error_message)

    def to_wire_content(self, tool: "Tool | None") -> str:
        """Render this envelope as the free-text a model sees for one tool call: any
        `system_interjections` as XML, error fields as `key: value` lines on failure only, then
        `response_body` rendered via `tool.format_response()` (a plain JSON dump if `tool` is
        `None`)."""
        parts: list[str] = []
        if self.system_interjections:
            parts.append("\n\n".join(
                wrap_system_interjection(i.subject, i.body) for i in self.system_interjections))
        if self.is_error:
            header_lines = [f"is_error: {_format_bool(self.is_error)}"]
            if self.error_category is not None:
                header_lines.append(f"error_category: {self.error_category}")
            header_lines.append(f"is_retryable: {_format_bool(self.is_retryable)}")
            if self.error_message is not None:
                header_lines.append(f"error_message: {self.error_message}")
            parts.append("\n".join(header_lines))
        if self.response_body is not None:
            formatted = (
                tool.format_response(self.response_body) if tool is not None
                else json.dumps(self.response_body, ensure_ascii=False))
            parts.append(formatted)
        return "\n\n".join(parts)


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
