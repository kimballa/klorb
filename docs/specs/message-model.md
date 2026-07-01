# Message model

## Summary

`klorb.message.Message` (`klorb/src/klorb/message.py`) is the unit of conversation history
in klorb: one message exchanged between the user, the model, or (in the future) tools. A
[[session-and-turns]] `Session` builds up a `list[Message]` as its conversation history, and
`ApiProvider` implementations (see [[openrouter-prompt-client]]) consume and produce
`Message`s. This is framework-level: any future feature that needs to inspect or render a
conversation (streaming output, tool calls, thinking blocks, a VSCode plugin) works against
this one shape.

## How it works

* `Message` is a `pydantic.BaseModel` with:
  * `content: str` — the message's text. For a streaming response, this is only final once
    `streaming_content` has been condensed into it.
  * `role: Role` — a `Literal["system", "user", "assistant", "thinking", "tool_defs",
    "tool_use", "tool_response"]`. Only `"user"` and `"assistant"` are produced today (see
    [[session-and-turns]]); the rest are reserved for tool-calling and reasoning-trace
    features that aren't implemented yet.
  * `num_tokens: int` — the token count attributed to this message. For an assistant reply,
    this comes directly from the provider's reported completion tokens; for a user message,
    it's derived after the fact (see [[derive-user-turn-token-counts-from-a-prompt-token-delta]]).
  * `processing_state: ProcessingState` — a `Literal["pending", "error", "started_receipt",
    "complete"]` tracking this message's lifecycle. `Session` mutates this in place as a
    turn progresses (see [[session-and-turns]] and
    [[mutate-the-same-message-on-turn-error-and-retry]]).
  * `timestamp: datetime` — when a user message was sent, or when a response (or the first
    chunk of a streaming response) was received.
  * `last_error: str | None` — set when `processing_state == "error"`, cleared on success.
  * `finish_reason: str | None` — the provider's reported stop reason, set on assistant
    replies.
  * `streaming_content: list[str] | None` — chunks of a streaming response as they arrive;
    condensed into `content` once complete, and reset to `None`.

## Out of scope

* Streaming (`streaming_content` is defined but nothing populates it yet — see
  [[openrouter-prompt-client]]'s out-of-scope note) and tool calling (`tool_defs`,
  `tool_use`, `tool_response`, `thinking` roles are defined but not yet produced or
  consumed by any code path) are not implemented yet.
