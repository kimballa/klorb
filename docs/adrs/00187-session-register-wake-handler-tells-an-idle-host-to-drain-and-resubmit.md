# `Session.register_wake_handler` tells an idle host to drain and resubmit

* Date: 2026-08-11
* Question: ADR-00186 fixed chained-turn delivery for `onAgentTurnEnd`/`onSubagentTurnEnd`,
  which always fire on the same call stack as whatever host's `send_turn()` is already
  blocking on — but left two trigger points broken: `Session.deliver_event_message`
  (`Timer`/`FileSystemModified`/`WorkspaceTrustChanged`) and `close()`'s `onSessionEnd`-triggered
  `reset_session`, both of which can fire with no turn in flight and so no host reference to
  hand a message to at all. Both just raised `ChainedHookMessageUndeliverableError`. How does a
  hook/event reach a user (or reset the conversation) when the session is sitting idle?
* Answer: `Session` grows a third long-lived, session-lifetime callback slot alongside
  `register_teardown`/`register_notice_handler`: `register_wake_handler(handler: Callable[[],
  None])`, single-slot like `register_notice_handler`, registered once by each host (TUI, ACP,
  headless) at session-init time, right where it already registers its notice handler. Unlike
  `register_notice_handler` (used only within `SessionCoreMixin` itself), `deliver_wake()` is
  called from `SessionTurnsMixin.deliver_event_message` in a different mixin file, so both the
  field and `deliver_wake()` are declared on `SessionBase` too.

  `deliver_event_message`, when idle, now enqueues the message (same `_queued_messages` queue
  ADR-00186 introduced) and calls `deliver_wake()` if a handler is registered, raising
  `ChainedHookMessageUndeliverableError` only when none is — e.g. a subagent, a `Session` built
  for a unit test, or headless CLI once `run_one_shot()`'s own loop has already returned.
  `_dispatch_event_entries`/`fire_workspace_trust_changed_hook` (the `Timer`/`FileSystemModified`/
  `WorkspaceTrustChanged` dispatch points) also start honoring `HookOutput.reset_session` for
  the first time, sharing one small helper (`_deliver_or_reset_event`) that resets the session in
  place then delivers the continuation via the same `deliver_event_message` path — reset first,
  since `_reset_state()` clears `_queued_messages`.

  Each host's wake handler does the same thing its own end-of-turn drain already does: call
  `Session.drain_next_turn_text()` and resubmit through its own front door. TUI posts a
  `TuiSessionWake` message (mirroring `TuiHistoryNotice`'s thread-safe hand-off) handled by
  `on_tui_session_wake`, which calls `_submit_prompt`. ACP hops onto its event loop via
  `asyncio.run_coroutine_threadsafe` and drives a fresh `TurnBridge.run_turn()` — the same
  mechanism `prompt()` uses, just self-triggered instead of client-triggered; the client sees
  ordinary `session_update` notifications regardless of which triggered it. Headless registers a
  no-op: `run_one_shot()`'s own loop already re-checks the queue after every turn on the same
  thread, so nothing needs to be pushed — registering at all is what makes `deliver_event_message`
  enqueue instead of raise while that loop is running. Once headless's loop finds nothing left
  queued, it returns and the process proceeds straight to `close()`; there is no idle window to
  wake.

  `close()`'s own `onSessionEnd`-triggered `reset_session` is not fixed by this mechanism — it's
  made explicitly unsupported instead. `HookDispatcher._run_chain` now drops `reset_session`
  from `onSessionEnd`'s aggregate result unconditionally (warned, the same shape as the existing
  "reset_session without a message" invariant), for both firing reasons
  (`SuspendSession`/`ResetSession`). `close()` simplifies to dispatching `onSessionEnd` for its
  handlers' side effects and `log` only, never branching on `reset_session`, and
  `ChainedHookMessageUndeliverableError`'s docstring narrows to describe only
  `deliver_event_message`'s no-registered-handler case.
* Reasoning: A registered callback mirroring `register_notice_handler` is exactly what TODO.md
  already named as the fix, and the shape generalizes cleanly: `Session` doesn't need to know
  *how* a host resubmits, only that it can be told to. Reusing `drain_next_turn_text()` means
  zero new drain logic — every host already has it.

  `onSessionEnd`'s case is different in kind, not just in when it fires: `deliver_event_message`'s
  idle case has a host that is merely *not currently running a turn* — it's still there,
  reachable, able to start one. `onSessionEnd` fires because a host has already decided this
  exact session is going away (real process/app exit, or `/clear`'s replacement) — waking it
  would mean that host aborting a shutdown it already committed to, a fundamentally different
  (and, for a mid-teardown host, often impossible) operation. No future wake mechanism changes
  that, so rather than leave `close()` executing a real state wipe and then raising, dropping
  `reset_session` centrally in the dispatcher means a hook author configuring `onSessionEnd` gets
  a clear signal (a warning) that the field does nothing there, instead of an exception surfacing
  from `close()` on every process exit.
