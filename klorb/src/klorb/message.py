# © Copyright 2026 Aaron Kimball
"""A single message exchanged between the user, the model, and tools within a session."""

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

MessageRole = Literal["system", "user", "assistant",
    "thinking", "tool_defs", "tool_use", "tool_response"]
"""Which participant a `Message` records: named `MessageRole` rather than `Role`, which
is `klorb.role.Role`."""

ProcessingState = Literal["pending", "error", "started_receipt", "complete", "aborted"]
"""`"aborted"` marks a message the user interrupted mid-stream (via Escape): unlike
`"error"`, nothing went wrong."""

MessageFragmentType = Literal["text", "image_url"]
"""The kinds of `MessageFragment` a `Message.fragments` entry can be. `"image_url"` matches
the OpenAI/OpenRouter content-part `"type"` value literally, so
`MessageFragment.model_dump()` stays a verbatim wire dict."""


class MessageFragment(BaseModel):
    """One part of a multi-part `Message.fragments` payload, so `Message.provider_content()`
    can hand `fragments` to a provider almost verbatim."""

    type: MessageFragmentType
    text: str = ""
    image_url: dict[str, str] | None = None
    """`{"url": "data:image/webp;base64,..."}` when `type == "image_url"` and this fragment's
    bytes are currently resident in memory. `klorb.session.restore.
    try_restore_session` rehydrates every restored image fragment's `image_url` from
    `image_path` immediately on load, so by the time a fragment reaches `to_wire_dict()` this
    is always populated again."""
    image_path: str | None = None
    """Path (relative to the owning session's own `sessions/<subdir>/` directory) this
    fragment's bytes were spilled to, once the session directory is claimed. `None`
    for a fragment that only ever exists in memory. Kept
    so it survives `Message.for_persistence()`'s `session.json` dump."""
    mime_type: str | None = None
    """This fragment's image MIME type, set alongside `image_path` so
    `try_restore_session` can rebuild `image_url`'s data URI without re-parsing it out of a
    prior `image_url` value."""

    source_filename: str | None = None
    """Original filename of an image fragment's source file, if known. Klorb-only bookkeeping, never
    sent to a provider."""
    original_width: int | None = None
    original_height: int | None = None
    """Dimensions of an image fragment before `klorb.images.prepare.prepare_image_for_model`
    resized it."""
    resized_width: int | None = Field(default=None, exclude=True)
    resized_height: int | None = Field(default=None, exclude=True)
    """Dimensions of an image fragment after `klorb.images.prepare.prepare_image_for_model`
    resized it."""
    estimated_tokens: int | None = Field(default=None, exclude=True)
    """This fragment's own token cost, from `estimate_image_tokens`, if `type ==
    "image_url"`. Klorb-only bookkeeping, excluded from the wire payload."""

    def to_wire_dict(self) -> dict[str, Any]:
        """This fragment's exact OpenAI/OpenRouter content-part dict."""
        if self.type == "text":
            return {"type": "text", "text": self.text}
        elif self.type == "image_url":
            assert self.image_url is not None, "image fragment sent to a provider with no image_url"
            return {"type": "image_url", "image_url": self.image_url}
        else:
            raise ValueError(f"Unexpected message fragment type: {self.type!r}")


class ToolCallRequest(BaseModel):
    """One tool call the model asked to make, attached to a `role="tool_use"` `Message`."""

    id: str
    name: str
    arguments: str
    """Raw JSON-encoded arguments exactly as the model returned them; parsed by whoever
    dispatches the call."""


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
    """An optional multi-part payload attached alongside (not instead of) `content`.
    When set, `provider_content()` sends `fragments` to the model provider in place
    of `content`; `content` itself still carries a plain-text rendering of the message
    for anything that only wants that, so most `content` readers need no changes to stay
    correct once a message also carries `fragments`."""

    role: MessageRole
    num_tokens: int
    """This message's own token count, from `klorb.token_estimate.estimate_tokens` run
    against its current content. Populated the moment content exists for a message.
    """
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
    array a provider returned alongside its plain-text reasoning, accumulated by
    index as chunks stream in. Preserved verbatim. `None` for
    every other role, and for a `"thinking"` message whose provider never sent this field."""

    def body(self) -> str:
        """A reasonable plain-text representation of this message's current substance,
        regardless of which of `fragments`/`streaming_content`/`content` is actually
        carrying it right now: `fragments` (JSON-stringified) if set, else the joined
        `streaming_content` chunks if still streaming, else `content`. Intended for
        callers that just want "the text of this message" without each having to
        duplicate that precedence themselves."""
        if self.fragments is not None:
            return json.dumps(
                [fragment.model_dump() for fragment in self.fragments], ensure_ascii=False)
        if self.streaming_content is not None:
            return "".join(self.streaming_content)
        return self.content

    def provider_content(self) -> "str | list[dict[str, Any]]":
        """This message's `content` field as sent to a model provider: `fragments` (each
        dumped to its wire-format dict) when set, else the plain `content` string."""
        if self.fragments is not None:
            return [fragment.to_wire_dict() for fragment in self.fragments]
        return self.content

    def for_persistence(self) -> "Message":
        """This message as it should be serialized to `session.json`: identical to
        `self`, unless it carries an image fragment whose bytes have already been
        spilled to disk (`image_path` set) while its in-memory `image_url` is still
        populated, in which case the returned copy clears that fragment's `image_url`,
        so `session.json` keeps the durable `image_path` reference only."""
        if self.fragments is None:
            return self
        if not any(
            fragment.type == "image_url" and fragment.image_path and fragment.image_url
            for fragment in self.fragments
        ):
            return self
        return self.model_copy(update={"fragments": [
            fragment.model_copy(update={"image_url": None})
            if fragment.type == "image_url" and fragment.image_path and fragment.image_url
            else fragment
            for fragment in self.fragments
        ]})
