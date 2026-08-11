# Deliver chained turns via the queued-message drain loop, not a bare recursive `send_turn()`

* Date: 2026-08-11
* Question: `Session.start_turn_or_enqueue()` (ADR-00179) auto-chains a `chat` handler's
  `onAgentTurnEnd`/`onSubagentTurnEnd`/event message by calling `send_turn(text)` directly from
  inside `Session` when no turn is in flight. In practice this renders nothing: neither the TUI
  nor the ACP server ever learn a new turn started, since the call carries no `TurnEventHandlers`
  and bypasses each host's own turn-submission entry point entirely — the chained turn streams
  into `self._messages` and out to the provider, but no UI callback ever fires. How should a
  chained turn actually reach a user?
* Answer: Don't dispatch it from inside `Session` at all. Enqueue it onto the same
  `_queued_messages` queue a user's typed-ahead message already uses
  (`Session.enqueue_queued_message`, now tagged `QueuedMessage.origin`), and let it be picked up
  by the drain-and-resubmit loop every host already runs at the end of a turn — TUI's
  `_finish_turn` (which calls `_submit_prompt`, the real Enter-key front door) and ACP's
  `TurnBridge.run_turn` (which already loops calling `send_turn()` again for anything left
  queued). Both are generic; they don't care why a message was queued. `Session.
  drain_next_turn_text()` is the new shared join-and-mark-continuation helper both hosts (plus
  the newly-added loops in `Session.run_one_shot` and `klorb.agents.policy._run_subagent_turn`)
  call. `start_turn_or_enqueue` is deleted: its "turn in flight → enqueue" half moves inline to
  each of its three former callers (now correctly fixed, not just enqueued blindly), and its
  "no turn in flight → dispatch bare" half — which had no live host to render into — is replaced
  by raising `ChainedHookMessageUndeliverableError` (from `Session.deliver_event_message` while
  idle, and from `close()`'s `onSessionEnd`-triggered `reset_session`) rather than silently
  running tool calls with nothing rendering the result.
* Reasoning: A chained turn originates deep inside `Session`'s own hook-dispatch code, which
  correctly has no reference to a TUI widget tree or an ACP session/update queue — but two of its
  three trigger points (`onAgentTurnEnd`, `onSubagentTurnEnd`) fire synchronously on the same call
  stack as whatever host is already blocked waiting on `send_turn()`'s return. That host's own
  end-of-turn drain loop is already the correct, already-tested vehicle for "resubmit something
  through the real front door" — it exists today for a user typing ahead while a turn runs. Routing
  through it means zero new UI plumbing: the chained turn renders exactly like an ordinary
  submission, with no pending/italic phase (nothing is enqueued until the current turn has fully
  ended). The two remaining trigger points (idle event delivery, `close()`'s reset) truly have no
  live host on the call stack, so there is nothing to hand the message to — failing loudly there
  beats the invisible dispatch this ADR removes, and is honest about a currently-unsupported
  combination pending the "push-and-wake-up" mechanism tracked in TODO.md.

  `Session._chained_hook_turns` (the `max_chained_hook_turns` cap counter) is no longer reset by
  `start_turn_or_enqueue`'s own recursive call unwinding — chained turns are now independent,
  decoupled `_dispatch_turn()` calls, not nested stack frames. It resets unconditionally at the
  top of every `_dispatch_turn()` instead (the one place every turn funnels through, including an
  aborted or errored one, neither of which ever reached `_fire_agent_turn_end_hook` to begin
  with), skipped only via a one-shot `_chain_continuation_pending` flag that
  `drain_next_turn_text()` sets when — and only when — every drained message is purely
  hook-originated. A real user or event message mixed into the same batch resets the counter like
  any ordinary turn would.
