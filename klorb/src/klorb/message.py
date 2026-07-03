# © Copyright 2026 Aaron Kimball
"""A single message exchanged between the user, the model, and tools within a session."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

MessageRole = Literal["system", "user", "assistant", "thinking", "tool_defs", "tool_use", "tool_response"]
"""Which participant a `Message` records: named `MessageRole` (matching the `Message.role`
field, itself named for the chat API's wire-format `"role"` key) rather than `Role`, which
is `klorb.role.Role` — the operating role a session's *agent* performs."""

ProcessingState = Literal["pending", "error", "started_receipt", "complete"]


class ToolCallRequest(BaseModel):
    """One tool call the model asked to make, attached to a `role="tool_use"` `Message`."""

    id: str
    name: str
    arguments: str
    """Raw JSON-encoded arguments exactly as the model returned them; parsed by whoever
    dispatches the call (see `klorb.session.Session`)."""


class Message(BaseModel):
    """
    One message in a session's conversation history.
    """

    content: str
    streaming_content: list[str] | None = None
    """
    Streaming responses arrive as a sequence of chunks in `streaming_content`. Once the
    body is complete, the chunks are condensed into `content` and `streaming_content` is
    set back to `None`.
    """

    role: MessageRole
    num_tokens: int
    timestamp: datetime
    "Timestamp user msg was sent, or beginning of streaming response was received."
    processing_state: ProcessingState
    last_error: str | None = None
    finish_reason: str | None = None

    tool_calls: list[ToolCallRequest] | None = None
    """Populated on a `role="tool_use"` `Message`: the tool call(s) the model requested."""

    tool_call_id: str | None = None
    """Populated on a `role="tool_response"` `Message`: the `ToolCallRequest.id` (from the
    preceding `tool_use` message) this is the result of."""
