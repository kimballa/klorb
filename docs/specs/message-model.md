# Message model

## Summary

`klorb.message.Message` (`klorb/src/klorb/message.py`) is the unit of conversation history
in klorb: one message exchanged between the user, the model, or a tool. A
[[session-and-turns]] `Session` builds up a `list[Message]` as its conversation history, and
`ApiProvider` implementations (see [[openrouter-prompt-client]]) consume and produce
`Message`s. This is framework-level: any future feature that needs to inspect or render a
conversation (streaming output, tool calls, thinking blocks, a VSCode plugin) works against
this one shape.

## How it works

* `Message` is a `pydantic.BaseModel` with:
  * `content: str` — the message's text. For a streaming response, this is only final once
    `streaming_content` has been condensed into it. On a `role="tool_use"` message it's
    whatever text (often empty) accompanied the tool call request; on a `role="tool_response"`
    message it's the tool's result, stringified (`Tool.apply()`'s return value as-is if it's
    already a `str`, otherwise `json.dumps()`'d), or `f"Error: {exc}"` if the call failed.
  * `role: MessageRole` — a `Literal["system", "user", "assistant", "thinking", "tool_defs",
    "tool_use", "tool_response"]`. `"system"` is a bookkeeping message
    (`Session._ensure_system_message()`) recording the session's resolved system prompt
    (see [[roles-and-system-prompts]]),
    inserted once at the very front of history; the model itself is sent the (freshly
    re-resolved) system prompt via `ApiProvider.send_prompt(system_prompt=...)`, not this
    message (see
    [the system-prompt bookkeeping ADR](../adrs/store-system-prompt-as-a-bookkeeping-message.md)).
    `"tool_defs"` is a bookkeeping message
    (`Session._ensure_tool_defs_message()`) recording the tool definitions offered for a
    session, inserted once right after the `"system"` message (or at the front of history if
    there isn't one); the model itself is offered tools via
    `ApiProvider.send_prompt(tools=...)`, not this message (see [[tool-framework]] and
    [[session-and-turns]]). `"tool_use"` is a model reply that requested one or more tool
    calls (`tool_calls` populated) in place of a plain `"assistant"` reply. `"tool_response"`
    is the result of one dispatched call (`tool_call_id` populated). See
    [the tool-calling wiring ADR](../adrs/wire-tool-calling-into-the-session-turn-loop.md).
  * `tool_calls: list[ToolCallRequest] | None` — populated on a `role="tool_use"` `Message`:
    the tool call(s) the model requested. `ToolCallRequest` (also in `klorb/src/klorb/message.py`)
    has `id: str`, `name: str`, and `arguments: str` (the model's raw, not-yet-parsed
    JSON-encoded arguments).
  * `tool_call_id: str | None` — populated on a `role="tool_response"` `Message`: the
    `ToolCallRequest.id` (from the preceding `tool_use` message) this is the result of.
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
    condensed into `content` once complete, and reset to `None`. `Session` is the layer
    that populates and condenses this (see [[session-and-turns]] and
    [[session-owns-in-progress-assistant-message-during-streaming]]) — `ApiProvider`
    implementations only emit raw text deltas, they don't touch `Message` state directly.

## Out of scope

* `tool_calls`/`tool_call_id` are only meaningful in combination with their matching role
  (`tool_use`/`tool_response` respectively); nothing enforces that at the type level, since
  `Message` is one shape shared by every role rather than a role-specific subtype.
