---
name: multithreaded-python
description: Decide how to share or synchronize mutable state across threads (or asyncio tasks) while writing or reviewing klorb source. Use whenever new code spawns a `threading.Thread`/`@work(thread=True)` worker or an `asyncio.to_thread` call, adds a field to `Session`/`ReplApp`/a tracker class that more than one thread can read or write, exposes a list/dict a background thread mutates to a caller on another thread, or publishes more than one related field describing the same state transition (e.g. a "state" plus its "result"). Also use when reviewing a diff that touches any of these shapes, or when a bug report smells like "two things happened at once" (a dropped message, a duplicate index, a stuck worker, a widget from the wrong session).
---

# Multithreaded Python in klorb

klorb runs many threads against shared, long-lived objects (`Session`, `ReplApp`, subagent
trackers). `docs/specs/threading-audit.md` is the historical record of eight real races found and
fixed in this codebase; this skill distills the failure patterns and fixes from that audit into
reusable checks so the same mistakes aren't repeated. Each pattern below names the audit finding
it came from and the real fix, so you can see the actual code shape rather than a hypothetical.

## The core misconception: the GIL is not a lock on your logic

The GIL makes a single attribute read/write or a single list/dict operation atomic. It does
**not** make a read-then-write pair atomic, does not order two threads' operations against each
other, and does not stop one thread from observing another mid-way through a multi-step or
multi-field update. Every pattern below is this kind of *logical* race — never a segfault or
corrupted memory, always a window where one thread observes another's state half-finished.

The guiding question for any new cross-thread state: **if a second thread ran one line earlier or
later than expected, what would the first thread see, and is that a state it already knows how to
handle?** If the answer is "a state that can't happen" (an index that also can't happen, a `state
== "finished"` with no `output`), that state can and eventually will happen anyway.

## Failure patterns and their fixes

### 1. Exposing a live shared collection instead of a snapshot

`Session._write_session_state_and_touch` passed the **live** `Session._messages` list straight
into pydantic serialization while another thread was still `.append()`-ing streamed tokens into
one of its elements — a torn write or a serializer error on a container resized mid-walk
(threading-audit.md finding 1). The fix: any code that needs to iterate or serialize a collection
another thread mutates takes a lock shared with the mutator and builds its own copy inside that
lock, rather than handing out a reference to the live container. `Session._messages_lock`
(`klorb/src/klorb/session/mixins/_base.py`) is that shared lock; both the persistence path and
`_send_and_receive`'s placeholder mutation take it.

The same hazard applies to *read-only* callers: `session.messages` is a documented "read once into
a local, don't call it twice expecting the same list" accessor for exactly this reason — a caller
that iterates the property expression itself races a concurrent mutation between iterations.

### 2. No identity check across an object swap

`clear_session` could rebind `ReplApp._session` to a fresh `Session` while a background turn
worker was still streaming against the *old* one. Nothing stopped the old turn's
`call_from_thread` callbacks from mounting widgets into the *new* session's history
(threading-audit.md finding 2). The fix has two halves: give the worker the exact `Session` object
it started with, and have every render callback no-op when `self._session is not that session` —
the same identity-check shape `ReplApp._ensure_turn_finished` already used for cancel-event
identity. Don't rely on "there's usually only one of these" when a background thread holds a
reference that a foreground thread can replace out from under it.

### 3. Blocking a thread a watchdog also depends on

`Session.close()`'s subagent-teardown cascade ran `thread.join(timeout=...)` sequentially on the
main event-loop thread — the same thread whose `set_interval` timer snoozes the liveness watchdog.
Blocking that thread for the join meant the watchdog stopped being snoozed and force-exited a
healthy, deliberately-quitting process (threading-audit.md finding 3). The fix wasn't a new
thread — it was making the known-blocking span explicit to the watchdog:
`LivenessWatchdog.suspended()` (`klorb/src/klorb/watchdog.py`) is a context manager a caller wraps
around a bounded blocking teardown, pausing expiry checks and re-snoozing on exit. If a piece of
code is going to block the thread a liveness/timeout mechanism depends on, that mechanism needs to
be told, explicitly, rather than hoping the block finishes before it notices.

### 4. Latching a signal on an object whose lifetime doesn't span it

`KlorbAcpAgent.cancel` read `session.active_cancel_event`, which the turn loop set to `None` the
instant one turn ended — including the gap between a finished turn and the next queued one
starting. A `session/cancel` arriving in exactly that gap found `None` and was silently dropped
(threading-audit.md finding 4). The fix moved the latch onto an object that outlives any single
turn (`self._cancel_requested` on the durable agent, checked before starting each chained
iteration) instead of a per-turn `threading.Event` that is `None` at precisely the boundary a
signal is likely to land.

### 5. Publishing related fields as two separate writes

`SubagentTracker.mark_finished` used to set `handle.state = "finished"` and then
`handle.output = output` as two attribute writes. A reader — and most readers took no lock at all
— landing between them saw `state == "finished"` with `output is None`, a combination some call
sites `assert`ed could never happen (threading-audit.md finding 5). The fix: one immutable value
object holding everything that changes together, assigned in a single attribute write.
`SubagentTurnOutcome` (`klorb/src/klorb/agents/runtime.py`) is `output` + `completed`; `state` and
`output` on `SubagentHandle` became read-only `@property`s derived from one `outcome` field, so a
reader can no longer observe one without the other. Whenever a docstring or comment says two
fields are "set together" or "always both present," that is a `dataclass`/`BaseModel` waiting to
happen, not a fact to trust two separate assignments to preserve.

### 6. Partial multi-field updates to config a reader spans

`_apply_workspace_config` used to `setattr` `workspace`/`read_dirs`/`write_dirs` one field at a
time on a live, shared `SessionConfig`, while a permission check on another thread could be
mid-way through reading all three for one decision — a security-relevant race, since a decision
computed against a half-updated table is wrong in the unsafe direction (threading-audit.md finding
6). Reassigning the whole `SessionConfig` object wasn't safe either: `ToolRegistry` holds a
reference to it for the life of a `Session` and never re-fetches, so swapping the outer object
would strand every future tool call on the stale one. The fix nested the fields that must be read
and written together into one frozen sub-object, `WorkspaceAccess`
(`klorb/src/klorb/session/config.py`), always replaced as a whole via
`apply_workspace_access(...)`/`workspace_access_snapshot()` rather than mutated field by field —
so a single read of the sub-object can never see a mix of two generations. Before reassigning an
object wholesale to fix atomicity, check whether anything holds a long-lived reference to the
*outer* object rather than re-fetching it each time; if so, atomicity has to move to a smaller,
always-replaced sub-object instead.

### 7. Unsynchronized read-modify-write counters

`Session._allocate_child_index` did `self._next_child_index += 1` from whichever thread created a
subagent; two concurrent creators could read the same value before either wrote it back, producing
two subagents with the same index (threading-audit.md finding 7). `+=` on a plain `int`/`float`
attribute is never safe across threads no matter how small the window looks. Use
`klorb.counter.AtomicCounter` (a lock-guarded `increment()`/`decrement()`/`get_value()`) for any
counter more than one thread can touch, instead of a bare attribute plus `+=`.

### 8. Manual lifecycle tracking where a weak reference would do

`ReplApp._tool_call_widgets` appended every widget it ever built and never removed one, so a
`Ctrl+O` toggle walked every widget from every cleared session for the life of the process
(threading-audit.md finding 8) — a leak in the same "who owns this object's lifetime" family as the
races above, just without a race. The fix made the tracking collection a `weakref.WeakSet` instead
of teaching every place a widget can be unmounted to also remember to prune this second index;
once nothing else holds a strong reference, the entry disappears on its own. When a collection's
only job is "let me look up something that's owned and unmounted elsewhere," prefer a weak
collection over hand-rolled eviction bookkeeping.

## Locking discipline: a lock only works if every side takes it

A lock guards a piece of state only if **every** reader and writer of that state takes it — a
writer that locks around its mutation buys nothing if a reader bypasses the lock (findings 1, 5,
and 6 above all had exactly one side taking a lock and the other side not). When adding a lock,
grep for every existing read of the field it protects, not just the write you're fixing.

Threads owned by the TUI app (`ReplApp` and anything reachable only from thread 1) are the
exception to "add a lock": they're single-writer by construction, so a background thread must
never touch them directly — route through `call_from_thread`/`post_message` instead of adding a
lock around foreground-only state.

## Testing races deterministically

Don't reach for `time.sleep()` to reproduce a window — it's flaky under load and doesn't
reproduce the actual interleaving. Two patterns already in the repo pin the interleaving instead:

* A `threading.Barrier` plus a monkeypatched counter to prove exactly one of two racing callers
  wins — `test_policy.py::test_dispatch_direct_message_is_atomic_under_concurrent_calls_for_the_same_dormant_subagent`.
* Holding the lock under test in the test body itself, to park a worker at a known point before
  asserting on the state around it — `test_subagent_finish_and_a_concurrent_enqueue_cannot_strand_the_message`
  does this with `SubagentTracker.dispatch_guard()`.

The inverse hazard is real too: a test that asserts on state one of `ReplApp`'s own recurring
timers also mutates is itself racing that timer and will flake under full-suite load even though
it's not testing a race on purpose. Suppress the relevant timer (e.g.
`_start_subagents_panel_timer`) and drive ticks explicitly when a test calls a `_tick_*` method
directly.

## Checklist when writing or reviewing a change

- [ ] Does any new thread/worker read or write a field also touched by another thread? Is every
      side of that field — not just the one you're adding — going through the same lock?
- [ ] Is a live list/dict ever handed to, or iterated by, a caller on a different thread than the
      one mutating it, instead of a snapshot taken under a shared lock?
- [ ] Can the object a background thread holds a reference to (a `Session`, a config) be replaced
      or swapped by another thread while the background thread is still running? If so, does the
      background thread check identity before publishing a side effect?
- [ ] Does any code block a thread that a watchdog/timeout mechanism also depends on to stay
      alive? If so, is that mechanism explicitly suspended/snoozed around the blocking span?
- [ ] Is a cancellation/signal flag latched on an object whose lifetime is shorter than the window
      the signal needs to be observable in (e.g. cleared at a turn boundary)?
- [ ] Do two or more fields change together to describe one state transition? If so, are they one
      atomically-assigned value object rather than two separate attribute writes?
- [ ] Does a multi-field object get mutated field-by-field while a reader might read several of
      those fields together? Does anything hold a long-lived reference to the outer object (so a
      wholesale swap can't fix it — only a nested, always-replaced sub-object can)?
- [ ] Any bare `+= `/`-= ` on a counter more than one thread can touch: should this be an
      `AtomicCounter` instead?
- [ ] Any collection whose only job is tracking objects already owned/unmounted elsewhere: would a
      `weakref.WeakSet`/`WeakValueDictionary` replace manual eviction bookkeeping?
- [ ] Does a new test for this reproduce the race with a `Barrier`/held lock/blocked fake, rather
      than `time.sleep()`? Does a new test that calls a `_tick_*`/timer-driven method directly
      suppress that timer first?
