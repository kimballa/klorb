# © Copyright 2026 Aaron Kimball
"""A single message exchanged between the user, the model, and tools within a session."""

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

MessageRole = Literal["system", "user", "assistant", "thinking", "tool_defs", "tool_use", "tool_response"]
"""Which participant a `Message` records: named `MessageRole` (matching the `Message.role`
field, itself named for the chat API's wire-format `"role"` key) rather than `Role`, which
is `klorb.role.Role` — the operating role a session's *agent* performs."""

ProcessingState = Literal["pending", "error", "started_receipt", "complete", "aborted"]
"""`"aborted"` marks a message the user interrupted mid-stream (via Escape): unlike
`"error"`, nothing went wrong — the message simply stopped short of a finished reply, and
whatever `content` it has is exactly what streamed in before the interruption."""

MessageFragmentType = Literal["text"]
"""The kinds of `MessageFragment` a `Message.fragments` entry can be. Only `"text"` exists
today; a future image/file-attachment fragment type extends this literal rather than
replacing it."""


class MessageFragment(BaseModel):
    """One part of a multi-part `Message.fragments` payload -- the same shape a chat API's
    `content: [{"type": "text", "text": ...}, ...]` content-part array uses, so
    `Message.provider_content()` can hand `fragments` to a provider almost verbatim. Only
    `type="text"` is implemented; other fragment types (e.g. images) will add their own
    fields alongside `text` once supported."""

    type: MessageFragmentType
    text: str


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

    content: str = ""
    streaming_content: list[str] | None = None
    """
    Streaming responses arrive as a sequence of chunks in `streaming_content`. Once the
    body is complete, the chunks are condensed into `content` and `streaming_content` is
    set back to `None`.
    """

    fragments: list[MessageFragment] | None = None
    """An optional multi-part payload attached alongside (not instead of) `content` -- e.g.
    `@mention`ed file attachments the user's prompt referenced (see
    `klorb.session.mixins.mentions.resolve_at_mentions` and
    docs/specs/at-mention-file-inlining.md). When set, `provider_content()` sends `fragments`
    to the model provider in place of `content`; `content` itself still carries a plain-text
    rendering of the message (e.g. the user's typed prompt) for anything that only wants
    that -- TUI rendering, char-count logging, etc. -- so most `content` readers need no
    changes to stay correct once a message also carries `fragments`."""

    role: MessageRole
    num_tokens: int
    """This message's own token count, from `klorb.token_estimate.estimate_tokens` run
    against its current content -- a client-side `tiktoken` count, not a provider-reported
    one. Populated the moment content exists for a message -- at construction for a
    bookkeeping/user/tool-response message, and on every streamed chunk for an in-progress
    assistant/thinking placeholder -- and treated as this message's definitive cost from then
    on, since no provider hands back a per-message breakdown to reconcile against (only an
    aggregate per request). See docs/adrs/count-every-message-tokens-client-side-with-
    tiktoken.md."""
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

    reasoning_details: list[dict[str, Any]] | None = None
    """Populated on a `role="thinking"` `Message`: the raw, structured `reasoning_details`
    array a provider returned alongside its plain-text reasoning (e.g. OpenRouter's
    `reasoning.text`/`reasoning.summary`/`reasoning.encrypted`-typed entries), accumulated by
    index as chunks stream in. Preserved verbatim -- never rendered or reinterpreted -- purely
    so it can be resent unmodified on a later turn, letting a reasoning-capable model verify
    and continue from its own prior reasoning trace instead of starting fresh. `None` for
    every other role, and for a `"thinking"` message whose provider never sent this field."""

    def body(self) -> str:
        """A reasonable plain-text representation of this message's current substance,
        regardless of which of `fragments`/`streaming_content`/`content` is actually carrying
        it right now: `fragments` (JSON-stringified) if set, else the joined
        `streaming_content` chunks if still streaming, else `content`. Intended for callers
        that just want "the text of this message" -- e.g. token-count estimation, debug
        char-count totals -- without each having to duplicate that precedence themselves."""
        if self.fragments is not None:
            return json.dumps([fragment.model_dump() for fragment in self.fragments])
        if self.streaming_content is not None:
            return "".join(self.streaming_content)
        return self.content

    def provider_content(self) -> "str | list[dict[str, Any]]":
        """This message's `content` field as sent to a model provider: `fragments` (each
        dumped to its wire-format dict) when set, else the plain `content` string."""
        if self.fragments is not None:
            return [fragment.model_dump() for fragment in self.fragments]
        return self.content
