# Session owns the in-progress assistant Message during streaming, provider stays dumb

* Date: 2026-06-30 00:00
* Question: When `ApiProvider.send_prompt()` streams a reply chunk-by-chunk, which layer is
  responsible for turning those chunks into conversation-history state — the provider, or
  `Session`? And once a stream can fail mid-flight, does `retry_last_turn()`'s assumption
  that the last errored turn is always `self._messages[-1]` still hold?
* Answer: `ApiProvider.send_prompt()` gains an optional `on_chunk: Callable[[str], None] |
  None` parameter, and `OpenRouterApiProvider` always streams internally (`stream=True`,
  `stream_options={"include_usage": True}`), but stays a pure request/response function: it
  still returns one final `ProviderResponse` and has no notion of conversation history.
  `Session._dispatch_turn()` wraps `on_chunk` with its own handler that, on the first
  chunk, appends a placeholder `Message` (`processing_state="started_receipt"`,
  `streaming_content=[]`) to `self._messages`, appends each subsequent chunk to it, and on
  completion finalizes that same object in place (`content`, `streaming_content=None`,
  `processing_state="complete"`) instead of appending a second assistant `Message`. On a
  mid-stream failure, the placeholder (if created) is also marked
  `processing_state="error"`, leaving its partial `streaming_content` intact.
  `Session.retry_last_turn()` no longer assumes the errored user `Message` is
  `self._messages[-1]`: it scans backward for the last `role=="user"` message, and if it's
  in `"error"` state, deletes everything after it (discarding any partial assistant
  fragment left by the failed stream) before resending.
* Reasoning: Keeping the provider ignorant of `Message`/history semantics preserves the
  existing `ApiProvider` abstraction boundary — any future provider can adopt the same
  `on_chunk` contract without knowing about `Session`'s bookkeeping. Making `Session` own
  placeholder lifecycle is what `Message.streaming_content`/`processing_state=
  "started_receipt"` were already designed for — a UI holding a live reference to
  `session.messages` sees the assistant reply appear and grow in real time. Finalizing in
  place (rather than appending a second `Message`) preserves the same
  one-semantic-turn-per-`Message` invariant established in
  [[mutate-the-same-message-on-turn-error-and-retry]]. The retry fix is necessary, not
  optional, precisely because that same invariant's assumption ("failure always happens
  before any assistant Message exists") no longer holds once a partial assistant fragment
  can outlive a failed attempt.
