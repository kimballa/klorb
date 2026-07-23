# © Copyright 2026 Aaron Kimball
"""`ToolResponseEnvelope`: the structured JSON object every `role="tool_response"`
`Message.content` is serialized from, in place of the bare result-or-`"Error: ..."` string
`klorb.session.mixins.tool_execution._run_tool_calls` used to build. Gives the model a uniform
set of fields to reason about a failed call with (`is_error`/`is_retryable`/`error_category`/
`error_message`) instead of guessing from prose, and a reserved slot for harness advisories
(`system_interjections`) and, in the future, queued user messages (`user_interjections`) to ride
along with a tool result instead of only a user-turn prompt. See
docs/specs/session-and-turns.md and docs/plans/archive/015-structured-tool-responses.md.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from klorb.tools.exceptions import ErrorCategory, NoSuchToolException, ToolCallError

_RETRYABLE_CATEGORIES: frozenset[ErrorCategory] = frozenset({"transient", "syntax", "validation"})


class SystemInterjectionPayload(BaseModel):
    """One standing-interjection provider's advisory, attached to a `ToolResponseEnvelope`'s
    `system_interjections` -- the JSON-delivered counterpart to the XML `<SystemInterjection
    subject="...">` block a user-turn prompt carries (see
    `klorb.session.mixins.turns._wrap_system_interjection`). `subject` mirrors the provider's
    registration key (`Session.register_standing_interjection`); `body` is the message it
    returned."""

    model_config = ConfigDict(frozen=True)

    subject: str
    body: str


class UserInterjectionPayload(BaseModel):
    """Reserved for a future plan: a queued user message delivered mid-tool-loop, attached to a
    `ToolResponseEnvelope`'s `user_interjections`. Nothing produces one of these yet -- see
    docs/plans/archive/015-structured-tool-responses.md's "Out of scope" section."""

    model_config = ConfigDict(frozen=True)

    user_message: str


class ToolResponseEnvelope(BaseModel):
    """The JSON object every `tool_response` `Message.content` is serialized from (via
    `to_wire_dict()`). Construct via `success()`/`error()`, never directly, so `is_retryable`
    always stays derived from `error_category` rather than being set independently."""

    model_config = ConfigDict(frozen=True)

    is_error: bool
    is_retryable: bool
    error_category: ErrorCategory | None = None
    error_message: str | None = None
    response_body: Any = None
    system_interjections: list[SystemInterjectionPayload] = Field(default_factory=list)
    user_interjections: list[UserInterjectionPayload] = Field(default_factory=list)

    @classmethod
    def success(
        cls,
        response_body: Any,
        *,
        system_interjections: "tuple[SystemInterjectionPayload, ...]" = (),
    ) -> "ToolResponseEnvelope":
        """Build a successful call's envelope. `is_retryable` is unconditionally `False` --
        a successful call (even one whose `response_body` is empty, e.g. a Grep with no
        matches) is never something to retry."""
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
        `_RETRYABLE_CATEGORIES`, `False` when `category` is `None` (an unclassified failure
        defaults to "don't retry blindly"). `message` is `None` for the one case where the
        failure detail already lives inside `response_body` (a `BashTool` command that ran but
        exited non-zero — see `SessionToolExecutionMixin._run_tool_calls`'s `is_success()`
        check) — duplicating it into `error_message` too would carry the same detail twice."""
        return cls(
            is_error=True, is_retryable=category in _RETRYABLE_CATEGORIES,
            error_category=category, error_message=message, response_body=response_body,
            system_interjections=list(system_interjections))

    def to_wire_dict(self) -> dict[str, Any]:
        """Serialize to the dict that becomes a `tool_response` `Message.content`'s JSON body:
        `model_dump(exclude_none=True)`, additionally dropping `system_interjections`/
        `user_interjections` entirely (not just as an empty `[]`) when empty -- every
        `tool_response` pays this envelope's overhead now, so the dominant (no-interjection)
        case shouldn't carry two empty-array keys on every single call."""
        wire = self.model_dump(exclude_none=True)
        if not self.system_interjections:
            wire.pop("system_interjections", None)
        if not self.user_interjections:
            wire.pop("user_interjections", None)
        return wire


def classify_exception(exc: Exception) -> tuple[str, ErrorCategory | None, Any]:
    """Return `(message, category, response_body)` for a tool-call exception, shared by every
    `except Exception` site in `klorb.session.mixins.tool_execution` and
    `klorb.session.mixins.permissions` (including retried-call exceptions) so categorization is
    never duplicated or missed on a retry path.

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
