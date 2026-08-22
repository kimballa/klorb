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
* **`reset_session()` could fire mid-turn from a timer/FS thread with no guard.** A running turn
  kept appending into a conversation `_reset_state()` had just rebound to a fresh list.
  `_deliver_or_reset_event` now gates every `reset_session` through `_prepare_reset_session`:
  `interrupt` false drops the reset while a turn is in flight, true sets that turn's
  `cancel_event` and waits for it to unwind first, the same as an Escape-then-new-prompt. The new
  `Session._messages_lock` guards the `_current_turn_handlers`/`active_cancel_event` transition
  at the start/end of `_dispatch_turn` against this decision, and is available for findings 1 and
  5 below to build on.
* **`SubagentHandle.state`/`output` were published as two separate writes.** A reader landing
  between `mark_finished`'s `handle.state = "finished"` and `handle.output = output` (or any of
  the TUI/ACP readers, which take no lock at all) could see `state == "finished"` with
  `output is None`. `policy._SubagentTurnOutcome` already had the right shape for this, so it
  moved to `agents.runtime` as the public, frozen `SubagentTurnOutcome` and became
  `SubagentHandle.outcome`, the single field `mark_finished` now assigns in one atomic write;
  `state`/`output` are properties derived from it, so a reader can never observe one without the
  other. `cascade_close_subagents`'s `handle.delivered = True` write, previously unlocked, now
  goes through a new `SubagentTracker.mark_delivered`.

## Open findings

*For each of these the situation is described, and then Claude proposes a sketch of how to*
*address the issue in "Claude's Fix direction." The user sometimes approves in whole or in part*
*by saying nothing; in other cases the user replies in "User's response", which should be taken*
*as superseding the recommendation in "Claude's fix."*

### 1. (FIXED) The session-naming thread serializes messages the turn thread is still streaming into

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

**Claude's Fix direction:** `persist_state()` should snapshot under a lock — take a `_messages` copy and
deep-copy (or `model_copy`) any message whose `processing_state == "started_receipt"` before
serializing. The cheapest correct version is for `_write_session_state_and_touch` to build its
own list of `for_persistence()` results while holding a messages lock that `_send_and_receive`
also takes around placeholder mutation. `Session._messages_lock` already exists for this — the
`reset_session`/`interrupt` fix above introduced it to guard `_current_turn_handlers`'s
transition — and finding 5 below can hang off it too.

**User's response:** Agree that a Session._messages_lock is the missing key to a LOT of this and is
long-overdue to install.

### 2. (FIXED) `clear_session()` replaces the session out from under a running turn

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

**Claude's Fix direction:** give `_send_prompt`'s worker the `Session` it started with and have every
`call_from_thread` render target no-op when `self._session is not that session` — the same
identity-check shape `_ensure_turn_finished` already uses for cancel events. Additionally, make
`_replace_session` refuse (or cancel-and-await) while `_turn_in_flight` is set.

**User's response:** Where you wrote "Additionally, make `_replace_session` refuse (or
cancel-and-await) while `_turn_in_flight` is set."... I disagree with the "Refuse" idea: the user wants a
new turn. Abort the current one first as if we ^C'd. Wait for that abort to be processed. Then do the
new one. (Because the user could have just killed the whole process and restarted `klorb --new`. They
want a clear session. Get there.)

### 3. (FIXED) `Session.close()` runs a multi-second cascade on the event-loop thread

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

**Claude's Fix direction:** run `close()`'s cascade off the event loop (a worker thread, awaited by the
quit path), or snooze the watchdog explicitly around a known-blocking teardown. Note that
`_collect_hang_diagnostics` deliberately calls `persist_state()` rather than `close()` for
exactly this reason — the quit path should be as careful.

**User's response:** I vote for "snooze the watchdog explicitly around known-blocking teardown. I
don't think introducing yet *another* thread* is the idea here.

Also fwiw 5 seconds is an eternity, do we actually need that long per subagent? They should all get
the notification to quit basically simultaneously. We should wait max 5 seconds wall clock time for
*all* of them, collectively. If that's too crazy to orchestrate, clamp on
_SHUTDOWN_JOIN_TIMEOUT_SECONDS, maybe 2 seconds?

### 4. (FIXED) An ACP `session/cancel` is silently dropped between chained turns

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

### 5. (FIXED) `SubagentHandle`'s `state`/`output` pair is published non-atomically

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

**Claude's Fix direction:** make the finish transition publish one immutable value — set `output` before
`state`, or better, replace the two mutable fields with a single `SubagentCompletion` object
assigned in one attribute write. Route `cascade_close_subagents`'s `delivered` write through a
tracker method that takes the lock.

**User's response:**
The fix described seems a lot like klorb.agent.policy._SubagentTurnOutcome, which was implemented in
the same commit that THREADING-AUDIT.md was authored. Can we reuse that? (Maybe it should not be a
_protected class, but should be more broadly available... dunno if it should actually be defined
where it is, in that case, or someplace more-universally-accessible.)

This should rely on `Session._messages_lock`, already introduced for the `reset_session`/
`interrupt` fix and finding 1's session-naming fix above.

### 6. (FIXED) `_apply_workspace_config` rewrites live config while a turn reads it

**Severity: medium-low.**

`KlorbAcpAgent._apply_workspace_config` (thread: ACP event loop) loops over every
`ProcessConfig.model_fields` doing `setattr(self._process_config, ...)`, then rewrites
`self._session.config.read_dirs`/`write_dirs` via `concat_dir_rules`, then calls
`reload_skills()`. `_klorb/trustWorkspace` can arrive at any moment, including while thread 14 is
inside a tool's permission check reading those exact tables.

A permission decision computed against a half-updated `read_dirs`/`write_dirs` pair is a
correctness problem in the security-relevant direction, even if the window is small.

**Claude's Fix direction:** build the new `SessionConfig`/`ProcessConfig` values off to the side and
publish them with single attribute assignments, so a reader sees either the old or the new table
and never a partially-concatenated one.

**User's response:** This is one where I am most skeptical that Claude's proposed fix direction
is a sound idea. Whereas other places I disagree above are more about choosing a different "product
direction" than the one implied by Claude's fix, Here I am just not even sure if that works.

The idea of building a new SessionConfig/ProcessConfig and then just saying e.g.
session.session_config = new_session_config for atomic reassignment seems nice. But do any tools or
anything else take a reference to the existing SessionConfig and survive for long enough for it to
be problematic to be stuck with the old session config? Or would they know to use the new one /
always go back to `session.config` rather than keeping their own maybe-stale `_session_config`
field?

**Resolution:** the skepticism was well-founded — `ToolRegistry` holds `session_config` by
reference for the life of a `Session` and never re-fetches it (its own docstring says as much:
"`session_config`... is held by reference and mutated in place elsewhere"), so reassigning
`self._session.config` wholesale would have stranded every future tool call on the stale object.
`SessionConfig`'s own identity stays untouched. Instead, `workspace`/`read_dirs`/`write_dirs`
moved into one nested field, `workspace_access: WorkspaceAccess` (a frozen model), always
replaced as a whole rather than mutated field-by-field: a single attribute read of
`workspace_access` can never observe a mix of two config generations, so
`workspace_access_snapshot()` needs no lock to be self-consistent for readers.
`apply_workspace_access(workspace=..., read_dirs=..., write_dirs=...)` builds a new
`WorkspaceAccess` and publishes it in one assignment; a private lock around both still prevents a
lost update between two concurrent writers (`_apply_workspace_config` and the interactive-grant
flow's `apply_permission_grant` can both compute a new value off the same stale read). `workspace`/
`read_dirs`/`write_dirs` stay available as read-only properties for the common single-field case,
each documenting that a caller needing two or more together should take one
`workspace_access_snapshot()` instead. `evaluate_write`/`resolve_and_evaluate_read`/
`resolve_and_evaluate_write` in `klorb.permissions.workspace` do exactly that: one snapshot up
front, reused for the whole check, rather than re-reading `context.session_config.*` at several
separate points. A `model_validator`/`model_serializer` pair keeps both the constructor
(`SessionConfig(workspace=..., read_dirs=..., write_dirs=...)`) and the on-disk `session.json`/
`klorb-config.json` shape unchanged, so this is purely an in-memory restructuring.

### 7. (FIXED) `Session._next_child_index` increments without synchronization

**Severity: low.** Display-only.

`_allocate_child_index` does `self._next_child_index += 1` — a read-modify-write — and is called
from a *child's* `__init__`, i.e. from whichever thread called `CreateSubagent`. Two subagents
(threads 6) creating children under the same parent concurrently can read the same value and both
receive the same `_child_index`, producing two agents that render the same dotted `address()`.

**Claude's Fix direction:** guard with a small lock, or allocate under the tracker's existing `_lock`.

**User's response:**

make an AtomicCounter util class in a counter.py somewhere:

```py
import threading

class AtomicCounter:
    def __init__(self, initial=0):
        self.value = initial
        self._lock = threading.Lock()

    def increment(self, step=1):
        # Ensures only one thread modifies the value at a time
        with self._lock:
            self.value += step
            return self.value

    def decrement(self, step=1):
        with self._lock:
            self.value -= step
            return self.value

    def get_value(self):
        with self._lock:
            return self.value

    def __repr__(self):
        with self._lock:
            return f"{self._value}"

    def __str__(self):
        with self._lock:
            return f"{self._value}"
```

... Then use that.

### 8. `ReplApp._tool_call_widgets` is never pruned

**Severity: low.** A leak rather than a race, but it is in the same "orphan widget" family and is
cheap to fix.

`_build_tool_call_widget` appends every widget it ever builds. Nothing removes an entry when the
history virtualizer collapses a chunk and unmounts its widgets, and `_do_replace_session` does not
clear the list (nor `_running_tool_call_widgets`) when it wipes the history. `Ctrl+O`
(`action_toggle_tool_call_detail`) therefore iterates every widget ever created in the process,
including unmounted ones from cleared sessions, calling `set_detail_shown` on each.

**Claude's Fix direction:** clear both collections in `_do_replace_session`, and drop entries when
the virtualizer unmounts a chunk (or hold them weakly).

**User's response:**

This seems like a great use of a weak ref array. Agree with explicit clear on _do_replace_session. Regarding eviction on virtualizer-unmount: make an assessment regarding how frequently that would run or how cumbersome (is it O(1) or O(n) to evict a given item?) that would be; if it would adversely impact virtualization performance, just let the weak refs do their job.

## Testing gaps

The existing suite covers cancellation and subagent dispatch well, but has almost no coverage of
the *cross-thread* interleavings above. Two patterns already in the repo are the right models to
extend:

* `test_policy.py::test_dispatch_direct_message_is_atomic_under_concurrent_calls_for_the_same_dormant_subagent`
  uses a `threading.Barrier` plus a monkeypatched counter to prove exactly one of two racing
  callers wins. That is the right shape for findings 2 and 7.
* Holding a lock in the test body to pin a worker at a known point (as this branch's
  `test_subagent_finish_and_a_concurrent_enqueue_cannot_strand_the_message` does with
  `dispatch_guard()`) deterministically reproduces a window without `sleep`-based flakiness.
  That is the right shape for findings 1 and 5.

The `reset_session`/`interrupt` fix above is covered the same deterministic way, but with a real
background thread instead of a lock: `test_hooks_fs_and_trust_events.py`'s
`test_dispatch_fs_modified_event_reset_session_interrupts_an_in_flight_turn` runs a real
`send_turn()` on its own thread, blocked in a fake provider until the main thread's
`_dispatch_fs_modified_event` call cancels and waits for it, and
`..._is_dropped_when_a_turn_is_in_flight_without_interrupt` covers the non-`interrupt` drop path.

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
