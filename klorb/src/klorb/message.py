# © Copyright 2026 Aaron Kimball
"""A single message exchanged between the user, the model, and tools within a session."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

MessageRole = Literal["system", "user", "assistant", "thinking", "tool_defs", "tool_use", "tool_response"]
"""Which participant a `Message` records: named `MessageRole` (matching the `Message.role`
field, itself named for the chat API's wire-format `"role"` key) rather than `Role`, which
is `klorb.role.Role` — the operating role a session's *agent* performs."""

ProcessingState = Literal["pending", "error", "started_receipt", "complete", "aborted"]
"""`"aborted"` marks a message the user interrupted mid-stream (via Escape): unlike
`"error"`, nothing went wrong — the message simply stopped short of a finished reply, and
whatever `content` it has is exactly what streamed in before the interruption."""


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
    estimated_tokens: int | None = None
    """A cheap, client-side token-count estimate (`klorb.token_estimate.estimate_tokens`) for
    this message's current content, or `None` once its true cost is accounted for by a real,
    server-reported count. Populated the moment content exists for a message -- at
    construction for a bookkeeping/user/tool-response message, and on every streamed chunk for
    an in-progress assistant/thinking placeholder -- so `Session.total_tokens_used()` has a
    live number to report before any round trip completes. Cleared back to `None` for every
    message in one sweep (`Session._settle_estimated_tokens()`) the moment any round
    completes, since that round's real `prompt_tokens` already covers every message sent as
    its input, however many earlier rounds contributed to it -- except a reply/thinking
    placeholder left `"aborted"` mid-stream, which was never part of a completed round's
    input or output and keeps this estimate permanently. See
    docs/adrs/null-estimated-tokens-when-a-real-count-supersedes-them.md."""
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
