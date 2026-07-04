# Keep an aborted turn's content in history, tagged `"aborted"`, instead of discarding it

* Date: 2026-07-03 15:30
* Question: [[escape-aborts-streaming-turn-and-discards-it-from-history]] deliberately
  discarded an aborted turn's user `Message` and any partial assistant/thinking placeholders
  entirely, on the grounds that tool calling didn't exist yet in `Session`. Tool calling has
  since landed (`Session._run_tool_calls`, dispatched from `_dispatch_turn`'s tool-call
  round loop), so an aborted turn can now include one or more earlier rounds' `tool_use`/
  `tool_response` messages whose tool calls already ran, with real side effects, before the
  round that got interrupted. Discarding the whole turn on abort — which is what the code
  still did — throws away the record of those completed side effects, exactly the failure
  mode the original ADR flagged as future work. Beyond that gap: should a user who presses
  Escape be able to see and keep whatever partial reply/thinking text and tool-call widgets
  had already rendered, rather than watching them vanish along with the echoed prompt?
* Answer: `ProcessingState` gains a new `"aborted"` variant, distinct from `"error"` (nothing
  went wrong) and `"complete"` (the reply never finished). `Session._send_and_receive()`, on
  catching `ResponseAborted`, now finalizes any assistant/thinking placeholder it created —
  folding `streaming_content` into `content` and setting `processing_state="aborted"` — the
  same way it finalizes a successful placeholder, rather than removing it from
  `self._messages`. `Session._dispatch_turn()` no longer deletes anything from
  `self._messages` on `ResponseAborted`; it only tags the turn's `user_message` as
  `"aborted"` too, giving it `num_tokens` from the last *completed* round's `prompt_tokens`
  if any round completed before the interruption (`0` otherwise). Any earlier round's
  `tool_use`/`tool_response` messages are untouched — they were already fully appended by
  `_run_tool_calls` and `_send_and_receive` before this round started streaming, so there is
  nothing to roll back. On the TUI side, `ReplApp._handle_aborted_response` no longer tears
  down the echoed prompt or any mounted widget for the turn; it tags whichever of the
  response/thinking widgets was still streaming with an "(interrupted)" marker (or mounts a
  standalone `.interrupted` marker if the abort landed before either had started, e.g. during
  an earlier round's tool dispatch), then clears the (already-empty) input box for the next
  message rather than repopulating it with the aborted prompt.

  One case still gets no special treatment: if the aborted round was itself a tool-call
  request rather than prose, `OpenRouterApiProvider.send_prompt()` accumulates
  `delta.tool_calls` fragments locally (per-index, across chunks) and never surfaces that
  partial accumulator to `Session` at all — `Session` only ever sees content deltas via
  `on_chunk`. So a truncated tool-call request (parts of a function name streamed but its
  JSON arguments only half-arrived) is simply never constructed as a `Message` in the first
  place; there's nothing for `Session` to keep or discard, and the TUI shows nothing for it
  beyond the general `.interrupted` marker. This isn't a gap to fix later: preserving a
  truncated tool-call request would mean inventing a `tool_use` message with possibly
  invalid JSON arguments and no matching `tool_response`, which violates the shape every
  provider's tool-calling API expects.
* Reasoning: Once real, non-undoable tool-call side effects can occur mid-turn, "discard the
  whole turn" and "this attempt didn't happen" (the framing the original ADR borrowed from
  `retry_last_turn()`) stop being an accurate model of what happened — some of it did happen,
  irreversibly, and `Session` losing track of it would leave the model unaware of an action
  it already took on a later turn. Plain truncated prose doesn't carry that risk: it's just
  text cut off mid-sentence, syntactically safe to keep and resend as context, and keeping it
  (clearly tagged as incomplete) matches user expectation of being able to read back what an
  agent was saying before it got interrupted or led astray, and lets them correct course
  without losing the thread. Tagging with a new `"aborted"` state (rather than reusing
  `"error"`) keeps the distinction between "something went wrong" and "the user chose to stop
  this" visible to any future code branching on `processing_state`, and keeps
  `retry_last_turn()`'s existing `"error"`-only retry condition unchanged — an aborted turn is
  not offered up for automatic retry, since the user, not `Session`, decides what to send
  next.
