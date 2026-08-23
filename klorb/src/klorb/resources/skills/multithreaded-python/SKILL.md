---
name: multithreaded-python
description: >
  How to share or synchronize mutable state across threads (or asyncio tasks) in Python, and the
  failure patterns that produce hard-to-reproduce bugs when this is done carelessly. Use whenever
  writing or reviewing code that spawns a thread or background task, adds a field or collection
  more than one thread can read or write, exposes a mutable collection to a caller on another
  thread, or publishes more than one related field describing the same state change. Also use
  when a bug report smells like "two things happened at once" (a dropped message, a duplicate
  value, a worker stuck forever, a decision made against half-updated state).
---

# Multithreaded Python

Concurrency bugs in Python are almost never memory corruption — the GIL rules that out for
single operations. They are **logical** races: one thread observes another mid-way through a
multi-step change and acts on a state that was never meant to be visible. These bugs are
notoriously hard to catch by testing because they depend on timing, and hard to catch by reading
because the code that's wrong often looks correct in isolation — the bug lives in the gap
*between* two lines that are each individually fine.

## The core misconception: the GIL is not a lock on your logic

The GIL guarantees that a single attribute read/write, and a single list/dict operation, are
atomic. It does **not** guarantee:

* that a read-then-write pair (`x.count += 1`, `if x is None: x = build()`) is atomic — another
  thread can run between the read and the write;
* that two threads' operations happen in any particular order relative to each other;
* that one thread can't observe another thread mid-way through a multi-field or multi-step
  update (object half-constructed, two related attributes set one line apart).

Every pattern below is this kind of race. None of them "shouldn't be possible because of the
GIL" — that reasoning is exactly the trap.

The guiding question for any new piece of cross-thread state: **if a second thread ran one line
earlier or later than expected, what would the first thread see, and is that a state the code
already knows how to handle?** If the answer is "a state that can't happen" — an index that
"can't" collide, a status flag that's "always" set together with its result — that state can and
eventually will happen anyway, under load, in production, at the worst time to debug it.

## Failure patterns and their fixes

### 1. Exposing a live, shared collection instead of a snapshot

Handing a caller on another thread a direct reference to a list/dict/set that a different thread
is still mutating is the single most common shape. The caller iterates it (or a serializer walks
it) while the owning thread appends, resizes, or removes an entry mid-walk — producing a
`RuntimeError: <container> changed size during iteration`, a torn read, or (worse) a silently
incomplete result with no exception at all.

```python
# Unsafe: caller iterates the live list while the writer thread still appends to it.
def get_recent_events(self) -> list[Event]:
    return self._events  # same list object the writer thread mutates

# Safe: take the lock the writer also takes, and copy while holding it.
def get_recent_events(self) -> list[Event]:
    with self._lock:
        return list(self._events)
```

Anything that will be serialized, summed, or otherwise walked more than once needs the same
treatment — deep-copy or snapshot any element that's itself still being mutated, not just the
outer container. An accessor that returns a fresh copy every call is easy to misuse too: a caller
that calls it twice expecting the same list back (or iterates the *expression* itself in a loop
condition) reintroduces the race one level up. Document "call once, use the local" on any such
accessor, and treat a second call to it as a race waiting to happen.

### 2. No identity/ownership check when the owner can be swapped out from under a background thread

A background thread often holds a reference to some "current" object (a session, a connection, a
config) captured when it started. If the *owner* of that concept can be replaced by another
thread while the background thread is still running, the background thread's later callbacks act
on the object it started with, but the outside world now considers a *different* object current —
so its side effects land in the wrong place.

```python
# Unsafe: worker assumes self.current is still the object it started with.
def _worker(self) -> None:
    result = do_work()
    self.current.record(result)  # self.current may have been replaced mid-flight

# Safe: capture identity at start, and no-op (or redirect) if it's gone stale.
def _worker(self, owner: Owner) -> None:
    result = do_work()
    if self.current is not owner:
        return  # owner replaced while we were working; this result is stale
    owner.record(result)
```

The same shape applies to cancellation tokens, futures, and any other per-operation handle: check
that the handle you're about to act on is still the *live* one, not just that it's non-`None`.

### 3. Blocking a thread that a watchdog/heartbeat mechanism also depends on

If a liveness check, timeout, or heartbeat is driven by a periodic callback on a particular
thread (very common in event-loop-based UIs and long-running services), and some other code path
blocks *that same thread* for a nontrivial, bounded, and legitimate reason (joining several
worker threads on shutdown, waiting on a slow but expected I/O call), the watchdog stops being
fed and can trip — killing a perfectly healthy process in the middle of a deliberate, bounded
wait.

The fix is not "spawn yet another thread to avoid blocking" — it's making the blocking span
**visible** to the mechanism that would otherwise misinterpret it:

```python
@contextmanager
def suspended(self) -> Iterator[None]:
    """Wrap a known-blocking span so it isn't mistaken for a hang."""
    self._paused = True
    try:
        yield
    finally:
        self._paused = False
        self.snooze()

# at the call site:
with watchdog.suspended():
    join_all_workers(timeout=SHUTDOWN_BUDGET_SECONDS)
```

Also bound the *total* wait across a collection of things being waited on, not a fixed timeout
*per item* — five 5-second per-item joins is a 25-second stall for something the caller thought
was bounded at 5 seconds. Signal every item to stop first, then wait once for the whole
collection within one shared budget.

### 4. Latching a signal on an object whose lifetime doesn't span the window it needs to cover

A cancellation flag, an event, or a similar per-operation token is often recreated fresh for each
operation and cleared/dropped the instant that operation ends. If an external signal (a cancel
request, an interrupt) can arrive in the gap between one operation ending and the next starting,
and the handler for that signal reads the current per-operation token to decide what to do, it
finds nothing to act on — the token that would have carried the signal doesn't exist yet, and the
one that just carried the previous operation doesn't matter anymore.

```python
# Unsafe: cancel() reads a per-operation token that is None between operations.
def cancel(self) -> None:
    if self._current_op_event is not None:
        self._current_op_event.set()
    # a cancel arriving here, between two chained operations, is silently dropped

# Safe: latch intent on something that outlives any single operation.
def cancel(self) -> None:
    self._cancel_requested = True
    if self._current_op_event is not None:
        self._current_op_event.set()

def _start_next_operation(self) -> None:
    if self._cancel_requested:
        return  # don't even start the next chained operation
    ...
```

Whenever a signal handler's correctness depends on catching a narrow ephemeral object at exactly
the right moment, move the latch to the durable object that supervises the whole sequence, and
have each new operation check it before starting.

### 5. Publishing two or more related fields as separate, non-atomic writes

When a state transition is described by more than one field (a status plus its result, a
position plus its total), setting them as two separate attribute assignments creates a window
where a reader — especially one that doesn't take the same lock the writer does — can observe
one without the other, including combinations the rest of the code assumes are impossible.

```python
# Unsafe: a reader between these two lines sees status == "done" with result still None.
self.status = "done"
self.result = value

# Safe: one immutable value object, published in a single attribute write.
@dataclass(frozen=True)
class Outcome:
    result: Any
    completed: bool

self.outcome = Outcome(result=value, completed=True)
# status and result become read-only properties derived from self.outcome
```

Any docstring or comment that says two fields are "always set together" is describing an
invariant that two separate assignments cannot actually guarantee — that's the signal to merge
them into one object assigned once.

### 6. Mutating a multi-field config object field-by-field while a reader spans several fields

Similar to #5 but for longer-lived shared config/state objects rather than one-shot results: if a
reader takes a decision based on reading two or more fields together (e.g. two directory lists
used jointly to decide a permission), and a writer updates those fields one `setattr` at a time,
a reader landing mid-update sees a decision-relevant combination that was never actually
intended — old value of one field paired with the new value of another.

Reassigning the *whole* outer object atomically looks like the fix, but check first whether
anything holds a long-lived reference to that outer object and never re-fetches it — if so, a
wholesale swap stops working for every holder of the stale reference. In that case, nest exactly
the fields that must be read/written together into one smaller, immutable sub-object, and always
replace that sub-object as a whole (never mutate its fields in place):

```python
@dataclass(frozen=True)
class AccessRules:
    read_dirs: tuple[str, ...]
    write_dirs: tuple[str, ...]

# readers always read the whole snapshot together:
rules = self.access_rules  # one attribute read, one generation, never a mix
# writers always replace the whole snapshot, never mutate a field:
with self._lock:
    self.access_rules = AccessRules(read_dirs=new_read, write_dirs=new_write)
```

A lock around the write side still matters even with an atomically-swapped object: it prevents
two concurrent writers from both computing a new value off the same stale read and one silently
clobbering the other's update (a lost update), which an atomic single-field swap alone doesn't
prevent.

### 7. Unsynchronized read-modify-write counters

`self._counter += 1` from more than one thread is a read-modify-write, not an atomic operation,
no matter how small and "obviously fine" it looks. Two threads can both read the same value
before either writes back, and one increment is lost — or, worse, two callers both receive the
"same" freshly-incremented value and treat it as uniquely theirs (a duplicate ID, a duplicate
index).

```python
class AtomicCounter:
    """A thread-safe integer counter: every read, increment, and decrement takes an internal
    lock so concurrent callers never race on the same read-modify-write."""

    def __init__(self, initial: int = 0) -> None:
        self._value = initial
        self._lock = threading.Lock()

    def increment(self, step: int = 1) -> int:
        with self._lock:
            self._value += step
            return self._value

    def get_value(self) -> int:
        with self._lock:
            return self._value
```

Use something with this shape (or `itertools.count()` guarded by a lock, or
`multiprocessing.Value` for cross-process cases) for any counter, ID generator, or index
allocator more than one thread can touch — never a bare attribute plus `+=`/`-=`.

### 8. Manual lifecycle tracking where a weak reference would do

A collection that exists purely to let one part of the system look something up that is already
owned and eventually discarded elsewhere (a widget registry, a listener list, a cache of
in-flight requests) tends to accumulate stale entries forever unless every single place that
retires the underlying object also remembers to prune this second index — and that symmetry is
easy to break as the codebase grows.

```python
# Leaky: every removal site has to remember to also pop from this dict.
self._by_id: dict[str, Handle] = {}

# Self-cleaning: once nothing else holds a strong reference, the entry disappears on its own.
self._by_id: "weakref.WeakValueDictionary[str, Handle]" = weakref.WeakValueDictionary()
```

Not a race, but the same underlying category of bug as the others: state that's supposed to
track another owner's lifecycle but is kept in sync by hand instead of by construction. Reach for
`weakref.WeakSet`/`WeakValueDictionary` before hand-rolled eviction logic whenever the tracked
object's real lifetime is already decided somewhere else.

## Locking discipline: a lock only protects state if every side takes it

A lock guards a field only if **every** reader and writer of that field takes it — a writer that
carefully locks around its own mutation buys nothing if even one reader bypasses the lock
(patterns #1, #5, and #6 above all commonly arise as exactly this: one side was fixed, the other
wasn't). When adding a lock to fix a race, grep for every existing read of the field being
protected, not just the write path you're changing.

The other valid discipline is **thread confinement**: state that only one specific thread (a UI
event loop, a single-threaded actor) is ever allowed to touch directly needs no lock at all — but
only if every other thread genuinely never touches it, routing instead through a message-passing
primitive (a queue, a `call_soon_threadsafe`-style hop, a posted event) that hands control back to
the owning thread. Confinement and locking are both correct; silently mixing them for the same
piece of state (some callers lock, others assume confinement) is what produces a race.

## Testing races deterministically

Don't reach for `time.sleep()` to reproduce a race — it's flaky under load, and a sleep that
"works" locally often doesn't reproduce the actual interleaving that causes the bug in
production. Two patterns pin the interleaving instead of hoping for it:

* **`threading.Barrier`** to force two or more threads to reach a specific point simultaneously,
  then assert on which one "won" a race that should have exactly one winner (or that both
  produced a consistent combined result).
* **Holding the lock under test in the test body itself** to park a background thread at a known
  point, assert on state while it's parked there, then release it — deterministically
  reproducing a window that would otherwise require exact timing.

The inverse hazard is just as real: a test asserting on state that a *different*, already-running
background timer or periodic task also mutates is itself racing that timer, and will flake under
load even though it isn't testing a race on purpose. If a test needs to drive a periodic/tick-
based method directly, suppress the real timer for the duration of that test first.

## Checklist when writing or reviewing multithreaded code

* [ ] Does any new thread/task read or write state also touched by another thread? Is every side
      of that state — not just the one you're adding — going through the same lock?
* [ ] Is a live list/dict/set ever handed to, or iterated by, a caller on a different thread than
      the one mutating it, instead of a snapshot taken under a shared lock?
* [ ] Can the object a background thread holds a reference to be replaced by another thread while
      the background thread is still running? If so, does the background thread check identity
      before publishing a side effect through it?
* [ ] Does any code block a thread that a watchdog/heartbeat/liveness mechanism also depends on to
      stay alive? If so, is that mechanism explicitly suspended/notified around the blocking span,
      and is the total wait bounded once across all items rather than per item?
* [ ] Is a cancellation/signal flag latched on an object whose lifetime is shorter than the window
      the signal needs to be observable in (e.g. cleared between two chained operations)?
* [ ] Do two or more fields change together to describe one state transition? If so, are they one
      atomically-assigned value object rather than two separate attribute writes?
* [ ] Is a multi-field object mutated field-by-field while a reader might read several of those
      fields together? Does anything hold a long-lived reference to the outer object (so only a
      nested, always-replaced sub-object fixes it, not a wholesale swap)?
* [ ] Any bare `+=`/`-=` on a counter, ID, or index more than one thread can touch: should this be
      a lock-guarded counter instead?
* [ ] Any collection whose only job is tracking objects already owned/retired elsewhere: would a
      weak collection replace hand-rolled eviction bookkeeping?
* [ ] Does a new test reproduce the race with a `Barrier`/held lock/blocked fake, rather than
      `time.sleep()`? Does a test that drives a timer-based method directly suppress the real
      timer first?
