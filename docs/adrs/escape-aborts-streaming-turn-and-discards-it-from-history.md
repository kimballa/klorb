# Escape aborts a streaming turn by cancelling it cooperatively, and discards it from history

* Date: 2026-07-01 12:00
* Question: When the user presses Escape while a response is streaming in, how does the
  in-flight `ApiProvider.send_prompt()` call actually get interrupted from another thread,
  and once it stops, does the aborted turn's user `Message` (and any partial
  assistant/thinking placeholders `Session` created for it) stay in `self._messages` — as
  an errored turn, the way a real failure does — or does it get removed as if it never
  happened?
* Answer: `ApiProvider.send_prompt()` gains an optional `cancel_event: threading.Event |
  None` parameter. `OpenRouterApiProvider` checks `cancel_event.is_set()` once per chunk
  received from the stream; if set, it calls `stream.close()` and raises a new
  `ResponseAborted` exception (defined in `klorb.api_provider`) instead of returning a
  `ProviderResponse`. `Session._dispatch_turn()` forwards the same `cancel_event` and
  catches `ResponseAborted` ahead of its generic `except Exception` handler: rather than
  mutating the user `Message` to `processing_state="error"` (the existing failure path),
  it removes the user `Message` and any assistant/thinking placeholders created for this
  turn from `self._messages` outright, then re-raises `ResponseAborted` for the caller.
  `ReplApp` creates a fresh `threading.Event` per submitted prompt, passes it into
  `Session.send_turn()`, and binds Escape (`action_abort_response`) to `.set()` it; on
  catching `ResponseAborted` back on the worker thread, it tears down every widget mounted
  for that turn (the echoed prompt, and any partial response/thinking widgets) and writes
  the original prompt text back into the now-re-enabled input box for editing.
* Reasoning: A blocking `for chunk in stream:` loop on a worker thread can't be interrupted
  by cancelling the Textual `Worker` object (`@work(thread=True)` doesn't preempt a live
  Python thread) or by raising into it from outside; a cooperative flag checked between
  chunks is the simplest mechanism that works within the existing streaming loop, and
  since chunks normally arrive at sub-second intervals, the abort takes effect promptly in
  practice. `stream.close()` matters beyond just breaking the local loop: without it, the
  underlying HTTP connection and any further billed generation on the provider side would
  keep running in the background even though nothing is left to consume it.
  Discarding (rather than erroring) the turn is the correct model of what happened: unlike
  a real failure, the user's prompt is coming right back for them to edit and resend, so
  leaving a truncated, never-finished assistant fragment sitting in `self._messages` would
  feed the model misleading context (a reply that looks intentionally cut off) on every
  later turn, and would double up on tokens once the edited prompt is resubmitted. This
  mirrors the "this attempt didn't happen" precedent `retry_last_turn()` already
  established for errored turns (see
  [[mutate-the-same-message-on-turn-error-and-retry]] and
  [[session-owns-in-progress-assistant-message-during-streaming]]), except here the turn is
  removed entirely rather than kept around to mutate and resend automatically, since the
  user — not `Session` — decides what to resend and when.
  This design covers today's codebase, where a turn is at most a user prompt plus a
  streamed assistant/thinking reply — no tool calls are dispatched by `Session` yet. If
  tool calling lands later and a tool call has already executed (and thus had a real,
  non-undoable side effect) before Escape is pressed, discarding the whole turn would be
  wrong: the executed tool call and its result must stay in history so the model doesn't
  lose track of an action it already took, and only the trailing, still-incomplete
  assistant text after it should be discarded. That's a deliberate limitation of this v1,
  not an oversight — implementing it now would mean designing rollback semantics for a
  tool-call representation that doesn't exist in `Session` yet.
