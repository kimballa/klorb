# ADR 00191

**Date:** 2026-08-14

**Question:** When the user types a new message and presses Enter while an agent turn is
mid-flight, the harness queues it. If the turn's next round ends with `finish_reason`
`tool_calls`, `_run_tool_calls()` delivered the queued message by attaching it as a
`user_interjections` field on the first `tool_response` envelope of that round. The model then
saw it as one more piece of tool-result data, not as the actual `role="user"` message it is. How
should a queued message be delivered into a running tool loop so the model ascribes it the
significance of a real user turn?

**Answer:** Drop `user_interjections` from `ToolResponseEnvelope` entirely (the field, the
`UserInterjectionPayload` class, and the `success()`/`error()` plumbing). `_run_tool_calls()` no
longer drains the queue. Instead, `_dispatch_turn()` calls `Session.deliver_queued_user_message`
after each tool-call round: it drains the queue (firing `on_send_queued_message` per message, as
before) and appends the drained text as a single `role="user"` `Message` immediately after that
round's `tool_response` messages. The next `_send_and_receive()` then carries the tool results
and the user's message together in one request payload, and the model sees an ordinary
`role="user"` turn.

**Reasoning:** The original `user_interjections` slot was a JSON-delivered sibling of
`system_interjections`, which made sense for harness advisories (which carry no `role` of their
own) but not for user speech — a user message already has a natural wire representation,
`role="user"`, and folding it into a tool result made the model treat it as tool data rather than
an instruction. Appending a real `role="user"` message right after the round's `tool_response`
messages keeps the same ordering the old mechanism aimed for (tool results first, then the user's
interruption) while giving the message its proper role. A side benefit: because the queued message
is now a genuine `role="user"` message, `build_session_replay` renders it as a `"prompt"`-kind
entry with no special handling, so `_replay_user_interjection_entries` (the one consumer that had
to reconstruct a prompt entry from the envelope's own `user_interjections` field) is deleted.
`system_interjections` on the envelope is unchanged — it still carries standing harness advisories,
which have no user-role representation to prefer.
