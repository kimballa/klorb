# Track pending interaction releases per-future, keyed by session

* Date: 2026-08-22 14:20
* Question: `ReplApp` held a single `_release_pending_interaction` callback: the closure that
  resolves the currently-mounted interaction panel's decision future with a safe default
  (deny / cancelled), releasing the worker thread parked inside `App.call_from_thread` waiting on
  it. Each `_confirm_*` method assigned it before awaiting and cleared it in a `finally`. That
  was correct when a `ReplApp` could only ever have one turn, and therefore one panel, in flight.

  Subagents broke the assumption. A subagent's turn runs on its own background thread and routes
  its permission/question/escalation asks through the creating session's callbacks
  (`klorb.agents.policy.build_subagent_turn_handlers`). Several sessions can therefore be parked
  awaiting a decision at once: `_reserved_interaction_slot` and `_await_session_selected` serialize
  which panel is *mounted*, but every waiting `_confirm_*` has already registered its release
  closure by then. With a single slot, whichever registered last overwrote the others.

  Two consequences. Aborting or quitting released only the most recently registered future, so
  every other parked worker thread stayed blocked on a decision that would never arrive — the
  exact failure ADR 00120 exists to prevent, reintroduced by a second concurrent asker. And
  Escape on a selected subagent set that subagent's `cancel_event` without releasing its panel,
  which that subagent's thread cannot observe while parked, so the interrupt did nothing until the
  user answered a panel they were trying to cancel.
* Answer: Replace the single slot with `ReplApp._pending_interaction_releases`, a dict keyed by
  `id()` of each decision future holding `(session_id, release)`. `_register_interaction_future`
  adds an entry, each `_confirm_*`'s `finally` drops its own via `_unregister_interaction_future`,
  and `_release_pending_interactions(session_id=None)` fires every registered release for one
  session, or all of them when passed `None`.

  Callers pick their scope by what they are actually cancelling: `_signal_turn_cancellation`
  releases the root session's, `_interrupt_running_activity` releases the selected subagent's
  alongside setting its `cancel_event`, and `_release_workers_for_exit` releases all of them,
  since a quit must strand nothing.
* Reasoning: Keying by future identity rather than by session id is what makes the registry
  correct rather than just larger. One session can legitimately have more than one future
  registered — a subagent whose ask is queued behind an already-mounted panel registers before
  the earlier one unmounts — so a session-keyed dict would reintroduce the same overwrite bug at
  a lower rate. The session id rides along as a value because it is what a scoped release needs
  to filter on, not because it identifies the entry.

  Firing a release is already idempotent: the `resolve` closure checks `future.done()` before
  setting a result, which ADR 00120 added for the stray-double-dismiss case. So releasing a
  future that a panel is concurrently resolving is safe, and a scoped release that over-matches
  costs nothing beyond a no-op.

  Iterating a snapshot (`list(...values())`) rather than the live dict matters: a release resolves
  a future, which can let a parked `_confirm_*` resume and drop its own entry, mutating the dict
  during iteration.
