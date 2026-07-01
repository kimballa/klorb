# © Copyright 2026 Aaron Kimball
"""A single message exchanged between the user, the model, and tools within a session."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Role = Literal["system", "user", "assistant", "thinking", "tool_defs", "tool_use", "tool_response"]

ProcessingState = Literal["pending", "error", "started_receipt", "complete"]


class Message(BaseModel):
    """One message in a session's conversation history.

    Streaming responses arrive as a sequence of chunks in `streaming_content`. Once the
    body is complete, the chunks are condensed into `content` and `streaming_content` is
    set back to `None`.
    """

    content: str
    role: Role
    num_tokens: int
    processing_state: ProcessingState
    timestamp: datetime
    last_error: str | None = None
    finish_reason: str | None = None
    streaming_content: list[str] | None = None
