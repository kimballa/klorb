# © Copyright 2026 Aaron Kimball
"""A single message exchanged between the user, the model, and tools within a session."""

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

MessageRole = Literal["system", "user", "assistant",
    "thinking", "tool_defs", "tool_use", "tool_response"]
"""Which participant a `Message` records: named `MessageRole` (matching the `Message.role`
field, itself named for the chat API's wire-format `"role"` key) rather than `Role`, which
is `klorb.role.Role` — the operating role a session's *agent* performs."""

ProcessingState = Literal["pending", "error", "started_receipt", "complete", "aborted"]
"""`"aborted"` marks a message the user interrupted mid-stream (via Escape): unlike
`"error"`, nothing went wrong — the message simply stopped short of a finished reply, and
whatever `content` it has is exactly what streamed in before the interruption."""

MessageFragmentType = Literal["text", "image_url"]
"""The kinds of `MessageFragment` a `Message.fragments` entry can be. `"image_url"` matches
the OpenAI/OpenRouter content-part `"type"` value literally, the same way `"text"` does, so
`MessageFragment.model_dump()` stays a verbatim wire dict."""


class MessageFragment(BaseModel):
    """One part of a multi-part `Message.fragments` payload -- the same shape a chat API's
    `content: [{"type": "text", "text": ...}, ...]` content-part array uses, so
    `Message.provider_content()` can hand `fragments` to a provider almost verbatim."""

    type: MessageFragmentType
    text: str = ""
    image_url: dict[str, str] | None = None
    """`{"url": "data:image/webp;base64,..."}` when `type == "image_url"` and this fragment's
    bytes are currently resident in memory -- `None` for a fragment restored from a persisted
    session whose bytes live on disk instead (`image_path` set). `klorb.session.restore.
    try_restore_session` rehydrates every restored image fragment's `image_url` from
    `image_path` immediately on load, so by the time a fragment reaches `to_wire_dict()` this
    is always populated again."""
    image_path: str | None = None
    """Path (relative to the owning session's own `sessions/<subdir>/` directory) this
    fragment's bytes were spilled to by `klorb.workspace.session_store.write_session_image`,
    once the session directory is claimed -- see docs/specs/session-persistence.md. `None`
    for a fragment that only ever exists in memory (an untrusted/unclaimed session). Kept
    (not `exclude=True`) so it survives `Message.for_persistence()`'s `session.json` dump --
    unlike `image_url`, which that dump clears once `image_path` is set, so a persisted image
    fragment's bytes live on disk exactly once, not duplicated inline as base64."""
    mime_type: str | None = None
    """This fragment's image MIME type (e.g. `"image/webp"`), set alongside `image_path` so
    `try_restore_session` can rebuild `image_url`'s data URI without re-parsing it out of a
    (by then already-cleared) prior `image_url` value."""

    source_filename: str | None = None
    """Original filename of an image fragment's source file, if known (e.g. dragged in from
    the OS file system rather than pasted from the clipboard). Klorb-only bookkeeping, never
    sent to a provider (`to_wire_dict()` never includes it) -- but kept out of `exclude=True`,
    unlike the fields below, so it survives persistence and rides along on a `_klorb/
    sessionReplay` restored prompt entry's `AttachedImageMeta` (see
    `klorb.server.update_mapping.build_session_replay`)."""
    original_width: int | None = None
    original_height: int | None = None
    """Dimensions of an image fragment before `klorb.images.prepare.prepare_image_for_model`
    resized it -- klorb-only bookkeeping, kept out of `exclude=True` (unlike `resized_width`/
    `resized_height` below) for the same session-replay reason as `source_filename`."""
    resized_width: int | None = Field(default=None, exclude=True)
    resized_height: int | None = Field(default=None, exclude=True)
    """Dimensions of an image fragment after `klorb.images.prepare.prepare_image_for_model`
    resized it -- klorb-only bookkeeping (`klorb.token_estimate.estimate_image_tokens`),
    excluded from `model_dump()`/the wire payload and never persisted: a resend of this
    fragment resizes again from the original bytes, so there's nothing that needs the
    already-resized dimensions to survive a restore."""
    estimated_tokens: int | None = Field(default=None, exclude=True)
    """This fragment's own token cost, from `estimate_image_tokens`, if `type ==
    "image_url"`. Klorb-only bookkeeping, excluded from the wire payload."""

    def to_wire_dict(self) -> dict[str, Any]:
        """This fragment's exact OpenAI/OpenRouter content-part dict -- `{"type": "text",
        "text": ...}` or `{"type": "image_url", "image_url": {"url": ...}}` -- regardless of
        which klorb-only bookkeeping fields (`image_path`, `source_filename`, ...) are also
        set on this fragment."""
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
            return [fragment.to_wire_dict() for fragment in self.fragments]
        return self.content

    def for_persistence(self) -> "Message":
        """This message as `klorb.workspace.session_store.write_session_state` should
        serialize it to `session.json`: identical to `self`, unless it carries an image
        fragment whose bytes have already been spilled to disk (`image_path` set) while its
        in-memory `image_url` is still populated -- in which case the returned copy clears
        that fragment's `image_url`, so `session.json` keeps the durable `image_path`
        reference only, not another copy of the (potentially multi-MB) base64 payload. See
        docs/specs/session-persistence.md."""
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
