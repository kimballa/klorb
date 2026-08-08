# Mutate the same Message in place on turn error and retry, rather than appending new ones

* Date: 2026-06-30 00:00
* Question: When a turn fails (the provider raises) or is later retried, should `Session`
  append a new `Message` per attempt, or update the original user `Message` in place?
* Answer: `Session.send_turn()` appends one user `Message` per turn
  (`processing_state="pending"`). On success it mutates that same object (`num_tokens`,
  `processing_state="complete"`, `last_error=None`) and appends the assistant's reply as a
  new `Message`. On failure it mutates the same object (`processing_state="error"`,
  `last_error=str(exc)`) and re-raises. `Session.retry_last_turn()` resends the most
  recent `processing_state=="error"` user `Message`'s content and mutates that same object
  again — it never appends a duplicate user `Message`. It raises `ValueError` if the most
  recent message isn't an errored user turn.
* Reasoning: One conversational turn is one semantic user message, regardless of how many
  times it had to be retried; appending a new `Message` per attempt would pollute the
  history sent to the model on every subsequent turn with duplicate near-identical user
  turns (and would double-count tokens). Mutating in place keeps the history's length equal
  to the number of actual conversational turns, and keeps `processing_state`/`last_error`
  an accurate, queryable record of that turn's current status.
