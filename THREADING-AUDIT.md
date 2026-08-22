# Threading audit: klorb's Python harness

An audit of every thread the klorb harness runs, where those threads touch shared mutable state,
and which of those interactions are actually synchronized versus relying on the GIL to paper over
a logical (not memory-level) race.

The GIL makes individual attribute reads/writes and single list/dict operations atomic. It does
**not** make a read-then-write pair atomic, does not order two threads' operations against each
other, and does not stop one thread from observing another mid-way through a multi-field update.
Every finding below is a logical race of that kind, not a memory-safety one.

Findings are ranked by severity. Each names the exact threads involved and the interleaving that
produces the bug, so it can be reproduced and fixed independently of the others.

## Thread inventory

| # | Thread | Started by | Lifetime |
| --- | -------- | ----------- | ---------- |
| 1 | Textual event loop (main thread) | `ReplApp.run()` | Whole TUI process |
| 2 | Turn worker | `@work(thread=True) _send_prompt` | One model turn |
| 3 | Shell worker | `@work(thread=True) _run_shell_command` | One `!command` |
| 4 | Shell stdout/stderr pumps (×2) | `UserShellCommand.run` | One `!command` |
| 5 | Session-naming classifier | `SessionCoreMixin._start_session_naming` | First turn of a session |
| 6 | Subagent turn worker (×N) | `agents.policy.dispatch_subagent_turn` | One subagent turn |
| 7 | Stream-closer | `OpenRouterApiProvider.send_prompt` | One streamed response |
| 8 | `PersistentShell` pumps (×2 per shell) | `PersistentShell.__init__` | Session-scoped bash shell |
| 9 | FS-watcher observer + debounce `Timer` | `hooks.fs_events.FileSystemWatcher` | Root session |
| 10 | `TimerScheduler` timers (×N) | `hooks.timer_events.TimerScheduler` | Root session |
| 11 | Liveness watchdog | `watchdog.LivenessWatchdog` | Whole TUI process |
| 12 | Force-exit cleanup | `watchdog.force_exit` | Bounded, during a force-exit |
| 13 | Workspace index / file index | `search_index.indexer`, `tui.workspace_file_index` | Root session |
| 14 | asyncio default executor (`asyncio.to_thread`) | ACP `TurnBridge.run_turn`, risk classifier | One call |

The ACP server replaces threads 1–3 with a single asyncio event loop plus `asyncio.to_thread`
workers, but threads 5–10 and 13 are identical, because they live on `Session`, not on the host.

**Rule of thumb this audit applies:** any state reachable from `Session` is touched by threads
2, 5, 6, 9, and 10 at minimum, and by thread 1 whenever the TUI renders it. Anything reachable
from `ReplApp` is owned by thread 1 and may only be touched from elsewhere via
`call_from_thread`/`post_message`.

## Fixed in this pass

These are already fixed on this branch; listed so a reader knows they are not open work.

* **`Session._queued_messages` had no lock.** `drain_queued_messages()` did
  `list(...)` then `.clear()` as two separate operations while threads 1, 9, 10, and 14
  called `enqueue_queued_message()`. A message appended between the copy and the clear was
  dropped with no trace. Now guarded by `Session._queued_message_lock`.
* **A subagent could finish with a message still queued on it.** Thread 6 drained its queue,
  found it empty, and called `mark_finished()`; a message enqueued between those two steps
  stranded forever, since nothing drains a finished subagent. The drain-and-finish decision now
  happens under the tracker's `dispatch_guard()`, the same lock every enqueue-vs-dispatch
  decision takes, so the message is either drained into one more turn or arrives after the
  state is `"finished"` and resumes the subagent.
* **`ReplApp` tracked only one pending interaction release.** `_release_pending_interaction`
  was a single slot, overwritten by whichever panel registered last. With a root turn and a
  subagent both awaiting a decision, aborting released only the most recent one and left the
  other worker parked in `call_from_thread` forever. Replaced with
  `_pending_interaction_releases`, keyed per future and tagged by session id, so an abort
  releases exactly the right ones and a quit releases all of them.
* **Escape on a selected subagent did not release its pending panel.** It set the subagent's
  `cancel_event`, which that subagent's thread cannot observe while parked awaiting a
  permission decision. It now also releases that session's pending interactions.
* **A failed ACP `session/update` killed the pump task.** `TurnBridge.run_turn`'s pump had no
  exception handling, so one delivery failure left every later item unacknowledged; the next
  blocking ask's `queue.join()` then parked the worker thread permanently. Delivery failures
  are now logged and the item still marked done.
* **`WaitForSubagent` ignored a message queued before it started.** It cleared
  `_user_msg_event` on entry, discarding the signal for an already-queued message, then blocked.
  It now checks the queue itself after clearing the event.

## Open findings

### 1. `reset_session()` fires from a timer/FS thread with no in-flight-turn guard

**Severity: high.** This is the clearest remaining instance of the "orphan agent keeps writing
into the live session" family.

`hooks.dispatcher` can return `HookOutput.reset_session`, and
`SessionCoreMixin._deliver_or_reset_event` (`klorb/src/klorb/session/mixins/core.py:996`) acts on
it unconditionally:

```python
if output.reset_session:
    cast("Session", self).reset_session()
    cast("Session", self).deliver_event_message(output.message)
```

`_deliver_or_reset_event` runs on thread 9 (the FS-watcher's debounce `Timer`) or thread 10 (a
`TimerScheduler` timer). `reset_session()` calls `_reset_state()`, which rebinds
`self._messages = []`.

Interleaving:

1. Thread 2 is inside `_dispatch_turn`, holding a local reference to `user_message` and
   streaming into a placeholder `Message`.
2. A `FileSystemModified` handler returns `reset_session`. Thread 9 calls `reset_session()`,
   which rebinds `self._messages` to a fresh empty list.
3. Thread 2's next `self._messages.append(...)` lands in the **new** list. The turn's
   `tool_use`/`tool_response` messages from before the reset are gone, but its assistant
   placeholder is now message #0 of what is supposed to be a blank conversation.
4. Thread 2 finishes and returns its response text to `_finish_turn`, which renders it into a
   history the reset was supposed to have cleared.

`_dispatch_event_entries` already computes `event_input.is_agent_active =
self.current_turn_handlers() is not None` and hands it to the hook — the harness knows whether a
turn is live, it just does not act on it.

**Fix direction:** treat a `reset_session` arriving mid-turn the same way a queued message is
treated — defer it to the turn boundary rather than applying it under the running turn. Either
queue the reset for the host's drain point, or have `_deliver_or_reset_event` refuse and log when
`current_turn_handlers() is not None`. `_reset_state()` rebinding `_messages` should additionally
assert no turn is in flight, so a future caller cannot reintroduce this.

### 2. The session-naming thread serializes messages the turn thread is still streaming into

**Severity: high.** Hits the first turn of every fresh session, which is why it would present as
intermittent corruption of new sessions specifically.

`_start_session_naming` (thread 5) ends with `self.persist_state()`.
`SessionPersistenceMixin._write_session_state_and_touch` passes `self._messages` — the **live
list**, not a copy — to `write_session_state`, which does
`[message.for_persistence() for message in messages]` and then pydantic-serializes each one.
`Message.for_persistence()` returns `self` in the common case, so pydantic serializes the live
object.

Meanwhile thread 2 is inside `_send_and_receive`'s `handle_chunk`, doing
`placeholder.streaming_content.append(delta_text)` on every streamed token.

Interleaving: thread 5's classifier returns (typically 1–10s in, squarely inside the first
turn's stream) → `persist_state()` → pydantic walks `placeholder.streaming_content` while thread
2 appends to that same list. Two outcomes, both bad: a torn `session.json` whose transcript is
missing messages appended during the walk, or a serializer error on a container that changed
size mid-iteration.

The same live-list exposure means `total_tokens_used()`/`total_output_tokens_used()` (called
from thread 1 via `_update_status_bar`) sum a list thread 2 is appending to.

**Fix direction:** `persist_state()` should snapshot under a lock — take a `_messages` copy and
deep-copy (or `model_copy`) any message whose `processing_state == "started_receipt"` before
serializing. The cheapest correct version is for `_write_session_state_and_touch` to build its
own list of `for_persistence()` results while holding a messages lock that `_send_and_receive`
also takes around placeholder mutation. Introducing a `Session._messages_lock` also gives
findings 1 and 6 somewhere to hang.

### 3. `clear_session()` replaces the session out from under a running turn

**Severity: high.** This is the other half of the orphan-agent family, and the one most likely to
be what produces "two widgets populating at once".

`PromptSubmissionMixin._replace_session` guards against *concurrent replacement*
(`_replacing_session`) but not against *replacement during a turn*: it never checks
`_turn_in_flight`, never sets `_cancel_event`, and never waits for thread 2 to unwind.
`_do_replace_session` then calls `old_session.close()`, builds a new `Session`, and does
`history.remove_children()`.

`clear_session` is reachable mid-turn: the `>clear` prompt route is blocked by the disabled input,
but `Ctrl+P` → `Clear session` is an app-level binding that fires regardless of focus.

Interleaving:

1. Thread 2 is streaming a response for the old session.
2. Thread 1 runs `_do_replace_session`: old session closed, `self._session` rebound,
   `history.remove_children()`, fresh virtualizer.
3. Thread 2's next `on_chunk` does `call_from_thread(self._mount_response_widget, ...)`, which
   mounts the *old* turn's `Markdown` into the *new* history.
4. Thread 2 eventually reaches `_finish_turn`, which drains the **new** session's queue, calls
   `self._session.persist_state()` on the **new** session, and clears `_turn_in_flight`.

`_ensure_turn_finished` compares cancel-event identity to avoid finishing a newer turn, but
nothing performs the equivalent check for the *session* the worker belongs to.

**Fix direction:** give `_send_prompt`'s worker the `Session` it started with and have every
`call_from_thread` render target no-op when `self._session is not that session` — the same
identity-check shape `_ensure_turn_finished` already uses for cancel events. Additionally, make
`_replace_session` refuse (or cancel-and-await) while `_turn_in_flight` is set.

### 4. `Session.close()` runs a multi-second cascade on the event-loop thread

**Severity: medium-high.** Can trip the liveness watchdog and force-exit a healthy process.

`KeyActionsMixin.action_quit` calls `self._session.close()` directly on thread 1. `close()` calls
`cascade_close_subagents`, which for each still-running subagent does
`handle.thread.join(timeout=_SHUTDOWN_JOIN_TIMEOUT_SECONDS)` — 5 seconds each, sequentially, and
recursively through the tree.

With two or more unresponsive subagents, thread 1 is blocked for 10s+. The watchdog
(`watchdog.timeout`, default 10s) is snoozed by a `set_interval` timer *on that same thread*, so
it stops being snoozed for exactly the duration of the join. The watchdog then fires and calls
`os._exit(1)` — during a clean, deliberate quit.

The same shape applies to `_do_replace_session`'s `old_session.close()`.

**Fix direction:** run `close()`'s cascade off the event loop (a worker thread, awaited by the
quit path), or snooze the watchdog explicitly around a known-blocking teardown. Note that
`_collect_hang_diagnostics` deliberately calls `persist_state()` rather than `close()` for
exactly this reason — the quit path should be as careful.

### 5. An ACP `session/cancel` is silently dropped between chained turns

**Severity: medium.**

`KlorbAcpAgent.cancel` reads `self._session.active_cancel_event` and no-ops when it is `None`.
`_dispatch_turn`'s `finally` clears that attribute the moment a turn ends. `TurnBridge.run_turn`
loops: after a turn ends it drains the queue and, if anything was queued, calls `send_turn()`
again with a **freshly constructed** `threading.Event`.

Interleaving: turn N ends → `active_cancel_event = None` → client sends `session/cancel` → the
handler finds `None` and returns → `run_turn` starts turn N+1 with a new event that nobody has
set. The user's cancel is lost and the agent keeps going.

The TUI does not have this bug because `ReplApp._cancel_event` is app-owned and only cleared in
`_finish_turn`.

**Fix direction:** latch the cancel on the agent (`self._cancel_requested = True`) and have
`TurnBridge.run_turn` check it before starting each chained iteration, rather than relying on a
per-turn attribute that is `None` exactly at the boundary where a cancel is most likely to land.

### 6. `SubagentHandle`'s `state`/`output` pair is published non-atomically

**Severity: medium-low.**

`SubagentTracker.mark_finished` sets `handle.state = "finished"` and then `handle.output = output`
under `self._lock`. Every TUI reader ignores that lock: `_tick_subagents_panel` (thread 1) reads
`handle.state`, `_mount_subagent_status_notice` reads `handle.output`, and
`KlorbAcpAgent._ext_subagent_transcript` reads both.

A reader landing between the two assignments sees `state == "finished"` with `output is None`.
`_mount_subagent_status_notice` handles it (`handle.output is not None and ...`), but
`runtime._format_relay_body` does `assert handle.output is not None`, and
`cascade_close_subagents` reads `handle.output`/`handle.delivered` and writes
`handle.delivered = True` entirely outside the lock while `has_undelivered()` reads it inside.

**Fix direction:** make the finish transition publish one immutable value — set `output` before
`state`, or better, replace the two mutable fields with a single `SubagentCompletion` object
assigned in one attribute write. Route `cascade_close_subagents`'s `delivered` write through a
tracker method that takes the lock.

### 7. `_apply_workspace_config` rewrites live config while a turn reads it

**Severity: medium-low.**

`KlorbAcpAgent._apply_workspace_config` (thread: ACP event loop) loops over every
`ProcessConfig.model_fields` doing `setattr(self._process_config, ...)`, then rewrites
`self._session.config.read_dirs`/`write_dirs` via `concat_dir_rules`, then calls
`reload_skills()`. `_klorb/trustWorkspace` can arrive at any moment, including while thread 14 is
inside a tool's permission check reading those exact tables.

A permission decision computed against a half-updated `read_dirs`/`write_dirs` pair is a
correctness problem in the security-relevant direction, even if the window is small.

**Fix direction:** build the new `SessionConfig`/`ProcessConfig` values off to the side and
publish them with single attribute assignments, so a reader sees either the old or the new table
and never a partially-concatenated one.

### 8. `Session._next_child_index` increments without synchronization

**Severity: low.** Display-only.

`_allocate_child_index` does `self._next_child_index += 1` — a read-modify-write — and is called
from a *child's* `__init__`, i.e. from whichever thread called `CreateSubagent`. Two subagents
(threads 6) creating children under the same parent concurrently can read the same value and both
receive the same `_child_index`, producing two agents that render the same dotted `address()`.

**Fix direction:** guard with a small lock, or allocate under the tracker's existing `_lock`.

### 9. `ReplApp._tool_call_widgets` is never pruned

**Severity: low.** A leak rather than a race, but it is in the same "orphan widget" family and is
cheap to fix.

`_build_tool_call_widget` appends every widget it ever builds. Nothing removes an entry when the
history virtualizer collapses a chunk and unmounts its widgets, and `_do_replace_session` does not
clear the list (nor `_running_tool_call_widgets`) when it wipes the history. `Ctrl+O`
(`action_toggle_tool_call_detail`) therefore iterates every widget ever created in the process,
including unmounted ones from cleared sessions, calling `set_detail_shown` on each.

**Fix direction:** clear both collections in `_do_replace_session`, and drop entries when the
virtualizer unmounts a chunk (or hold them weakly).

## Testing gaps

The existing suite covers cancellation and subagent dispatch well, but has almost no coverage of
the *cross-thread* interleavings above. Two patterns already in the repo are the right models to
extend:

* `test_policy.py::test_dispatch_direct_message_is_atomic_under_concurrent_calls_for_the_same_dormant_subagent`
  uses a `threading.Barrier` plus a monkeypatched counter to prove exactly one of two racing
  callers wins. That is the right shape for findings 1, 3, and 8.
* Holding a lock in the test body to pin a worker at a known point (as this branch's
  `test_subagent_finish_and_a_concurrent_enqueue_cannot_strand_the_message` does with
  `dispatch_guard()`) deterministically reproduces a window without `sleep`-based flakiness.
  That is the right shape for findings 2 and 6.

Deterministic windows beat timing: prefer a lock, `Barrier`, or a monkeypatched provider that
blocks on an `Event` the test controls, over `time.sleep`. Every test added on this branch
follows that rule and none of them are timing-dependent.

The inverse hazard is worth naming too, because it already bit this branch: a TUI test that
asserts state one of `ReplApp`'s own recurring timers also mutates is racing that timer, and will
flake under full-suite load rather than in isolation.
`test_selecting_a_subagent_does_not_lose_a_message_appended_mid_render` asserted
`_subagent_history_rendered_count == 1` while the 0.6s `_tick_subagents_panel` interval was live;
a tick landing first renders the appended message and legitimately makes it `2`. It now suppresses
`_start_subagents_panel_timer` and drives ticks itself. Any test that calls a `_tick_*` method
explicitly should suppress its timer for the same reason.

Worth adding regardless of the findings above: a test that asserts `Session` never exposes
`_messages` itself to a caller that iterates it on another thread — the "accessor returns a fresh
copy each call" hazard that already bit `_append_new_subagent_messages` and
`_render_full_subagent_transcript`, both of which now read `session.messages` once into a local.
That pattern is correct but unenforced; a reviewer will reintroduce it. A lint rule or a focused
test on the two virtualizer call sites would hold the line.
