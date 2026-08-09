# `chat` hook/event handlers decide start-vs-queue themselves via `Session.start_turn_or_enqueue`

* Date: 2026-08-08
* Question: `Session.send_turn()` is already the shared "start a turn" primitive both the TUI and
  ACP server call directly, and `Session.enqueue_queued_message()` already exists for queuing an
  interjection onto an in-flight turn. Both existing callers decide which one to use *outside*
  `Session` — the TUI checks `_turn_in_flight` before choosing `_submit_prompt` vs.
  `_queue_prompt`; the ACP protocol rejects a concurrent `session/prompt` outright, pushing a
  client toward `_klorb/enqueueMessage` instead. A `chat` hook/event handler's message needs the
  same start-vs-queue decision, but a hook/event dispatcher has no textarea, no client, nothing
  external that already knows whether a turn is in flight. Where should that decision live?
* Answer: A new `Session`-level method, `start_turn_or_enqueue(text)`: checks
  `current_turn_handlers()` (the same turn-in-flight signal `enqueue_queued_message`/
  `drain_queued_messages` already key off) and calls either `send_turn()` or
  `enqueue_queued_message()`. Both `type=chat` hook handlers and event delivery
  (`deliver_event_message`) go through this one method.
* Reasoning: The TUI and ACP server don't lack this decision because `Session` already makes it
  for them — they lack it because something *external* to `Session` (a human at a keyboard, a
  JSON-RPC client) already knows the answer before ever calling in. A hook/event dispatcher has no
  such external signal: it fires from inside `Session`'s own lifecycle, so the check-and-branch
  has to live inside `Session` too. This is the one new piece of library plumbing the feature
  needs, not a new way to start a turn — `send_turn()`/`enqueue_queued_message()` are unchanged.
  Bounding it with `SessionConfig.max_chained_hook_turns` (docs/specs/hooks-and-events.md's
  "Chained turns" section) keeps a misconfigured or missing `filter` on a `chat` handler from
  auto-chaining turns forever, the same fail-safe shape `max_tool_calls_per_turn` already uses for
  a different runaway-loop risk.
