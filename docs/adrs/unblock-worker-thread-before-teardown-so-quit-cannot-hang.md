# Unblock the turn worker thread before teardown so quitting cannot hang

* Date: 2026-07-14 01:51
* Question: Two linked symptoms were reported. (1) A model turn could "hang" indefinitely —
  the worker thread appeared stuck with nothing making progress. (2) Quitting with Ctrl+Q while
  a turn was hung would run the save-session prompt, save, tear down the TUI and restore the
  outer terminal — but the *process* would not exit. It sat there until the user pressed Ctrl+C,
  which finally released them back to the shell. Both trace to the same mechanism.

  Every model turn runs on a Textual **thread worker** (`ReplApp._send_prompt`, `@work(thread=
  True)`), and that thread talks to the UI only through `App.call_from_thread`, which is
  blocking: it does `asyncio.run_coroutine_threadsafe(...).result()` and parks the worker thread
  until the scheduled coroutine finishes on the event loop (Textual 8.2.8, `app.py`). When a
  tool call hits an `"ask"`, the worker calls `_on_permission_ask` → `call_from_thread(
  _confirm_permission_ask, ...)`; that coroutine takes `_interaction_lock` and `await`s a
  `decision_future` resolved only when the user dismisses the panel. So the worker is parked
  inside `future.result()` for as long as the panel is unanswered.

  If that `decision_future` is ever left unresolved — a stray second dismiss raising
  `InvalidStateError` on the first (the panel's own `set_result` was not idempotent), a panel
  orphaned by a racing teardown, etc. — the coroutine sleeps forever holding `_interaction_lock`,
  the worker's `call_from_thread` never returns, and the model is "hung"; every later interaction
  then also deadlocks on the held lock. And on quit, `self.exit()` stops the message loop while
  the worker is still parked in `future.result()`: the coroutine can now never complete, so the
  worker thread never returns, and because Textual thread workers run in asyncio's **non-daemon**
  default `ThreadPoolExecutor`, interpreter/loop shutdown joins that thread and blocks until the
  blocking call is interrupted — the "TUI is gone but the process won't end" hang. Two
  aggravators: Ctrl+C during a model turn fell straight through to quit *without* signalling the
  turn to stop, and `_cancel_event` was only consulted mid-stream (`openrouter.send_prompt`),
  never at a tool-call/round boundary, so a turn between streams ignored it.

  How do we make a turn interruptible and make quit reliably terminate the process, without
  reworking the threading model?
* Answer: Never let a worker thread outlive the event loop. Three coordinated changes:

  1. **Idempotent, teardown-releasable interaction futures.** `ReplApp._register_interaction_
     future` wraps each panel's `on_dismiss` so resolving is a no-op once the future is done
     (killing the double-dismiss `InvalidStateError` that could strand the future), and records a
     `_release_pending_interaction` closure that resolves that same future with a safe default
     (permission → `deny/once`, ask-user-questions → `cancelled`, escalate → `approved=False`).
     All three `_confirm_*` methods register on entry and clear in a `finally`.

  2. **Deferred exit.** `_begin_exit` replaces bare `self.exit()` on every quit path (Ctrl+Q's
     `_quit_after_maybe_saving`, `:q`/`/quit`/`/exit`). If nothing is in flight it exits
     immediately; if a turn or shell command is running it sets `_exit_requested`, calls
     `_release_workers_for_exit` (sets `_cancel_event`/`_shell_cancel_event` and fires
     `_release_pending_interaction`), and returns *without* exiting. The signalled worker unwinds
     and reaches `_finish_turn`, which performs the real `self.exit()` — by then the thread has
     finished and can no longer be stranded.

  3. **Cancellation honoured at round/tool boundaries.** `Session._dispatch_turn` checks
     `cancel_event` at the top of each tool-call round and `_run_tool_calls` before each call,
     raising `ResponseAborted`, so a set cancel unwinds the turn promptly instead of waiting for
     the provider's next mid-stream check.

  Ctrl+C (`action_interrupt`) now aborts an in-flight model turn (via `_signal_turn_cancellation`,
  = cancel event + release pending interaction) instead of quitting; it only quits when nothing
  is running. Rejected: making the worker threads daemon (asyncio's default executor owns them,
  and abandoning a thread mid-`call_from_thread` risks writing to half-torn-down UInternals);
  joining the worker from the event loop on exit (blocks the very loop the worker needs to make
  progress); and a hard timeout on `call_from_thread` (papers over the deadlock instead of
  removing it).
* Reasoning: The root property we restore is that a thread worker only ever blocks on the event
  loop for work the loop will actually complete. Making futures idempotent-and-releasable closes
  the orphan/double-resolve window that produced the primary "model hangs" deadlock, and gives
  teardown a lever to free a parked worker. Deferring the exit to `_finish_turn` — the single
  choke point every model turn and shell command already funnels through — means quit waits for
  the worker to wind down through the *normal* path rather than racing it, so `self.exit()` runs
  only when no thread is parked in `call_from_thread`. The boundary-level cancel checks are what
  make that wind-down prompt rather than "eventually, at the next stream." A parked `push_screen_
  wait` modal (`_confirm_tool_call_limit`) is the same shape but a much narrower state (only while
  the tool-call-cap modal is up, which is always on-screen and answerable); it is not released by
  `_release_workers_for_exit` today and remains a known, minor follow-up. Pinned to Textual
  8.2.8's blocking `call_from_thread` / non-daemon default-executor behaviour.
