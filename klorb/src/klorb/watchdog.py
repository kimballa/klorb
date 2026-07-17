# © Copyright 2026 Aaron Kimball
"""Last-ditch process force-exit for a wedged klorb: a shared `force_exit` (best-effort,
time-boxed cleanup then `os._exit`) and a `LivenessWatchdog` daemon thread that triggers it when
the main-thread event loop stops snoozing it. UI-agnostic library code — the TUI wires it up in
`klorb.tui.ReplApp`, but nothing here imports Textual. See
docs/specs/interrupt-and-liveness-watchdog.md and
docs/adrs/liveness-watchdog-over-reactive-arming.md.
"""

import os
import sys
import threading
import time
from collections.abc import Callable
from typing import NoReturn

_DEFAULT_MAX_POLL_SECONDS = 1.0
"""Upper bound on how often `LivenessWatchdog`'s own thread wakes to check for a lapse. The
effective poll is `min(this, timeout / 4)` so a small `timeout` still gets several checks."""


def run_bounded_cleanup(cleanup: Callable[[], None], grace_seconds: float) -> None:
    """Run `cleanup` on a throwaway daemon thread and wait at most `grace_seconds` for it to
    finish. Any exception `cleanup` raises is swallowed. Returns once `cleanup` finishes or the
    grace elapses, whichever comes first — so a cleanup that itself wedges (e.g. a session save
    blocked on the very resource that caused the hang) can never prevent the caller from
    proceeding to exit. Split out from `force_exit` so the bounding behavior is testable without
    actually terminating the process."""

    def _guarded() -> None:
        try:
            cleanup()
        except Exception:
            pass  # Best-effort: this only runs on the way out of a doomed process.

    worker = threading.Thread(target=_guarded, name="klorb-force-exit-cleanup", daemon=True)
    worker.start()
    worker.join(grace_seconds)


def force_exit(
    cleanup: Callable[[], None], grace_seconds: float, *, exit_code: int = 1,
) -> NoReturn:
    """Run `cleanup` (time-boxed to `grace_seconds` via `run_bounded_cleanup`) and then hard-exit
    the process with `os._exit(exit_code)`, bypassing all atexit hooks, `finally` blocks, and
    Textual teardown. The last-ditch escape for a wedged klorb, shared by the double-Ctrl+C
    handler and `LivenessWatchdog`. `os._exit` is deliberate: a wedged interpreter can't be
    trusted to run a normal `sys.exit`/teardown path, so nothing beyond the best-effort cleanup
    is attempted."""
    run_bounded_cleanup(cleanup, grace_seconds)
    print("Watchdog forced exit!", file=sys.stderr, flush=True)
    os._exit(exit_code)


class LivenessWatchdog:
    """A daemon thread that force-exits the process if it isn't `snooze()`'d at least once every
    `timeout_seconds`. The owning event loop is expected to snooze it from a timer running on the
    main thread; the snooze therefore stops exactly when the main-thread event loop wedges, which is
    the one hang mode neither key-event handling nor an external terminal `Ctrl+C` can escape
    (see docs/specs/interrupt-and-liveness-watchdog.md).

    Because snoozing is driven by the event loop and not by any worker-thread activity, a slow or
    silent model turn (which blocks only the worker thread) does not affect it — the main-thread
    loop keeps servicing its timer and keeps snoozing.

    `on_expire` is invoked exactly once, from the watchdog's own thread, when a lapse is detected;
    in production it is a `force_exit(...)` call (which never returns). `timeout_seconds <= 0`
    disables the watchdog: `start()` becomes a no-op, so no background thread is created at all —
    an escape hatch for users, and a way for tests to avoid a thread that could `os._exit` the
    test process.
    """

    def __init__(
        self,
        timeout_seconds: float,
        on_expire: Callable[[], None],
        *,
        poll_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._timeout: float = timeout_seconds
        self._on_expire: Callable[[], None] = on_expire
        self._clock: Callable[[], float] = clock
        self._poll: float = (
            poll_seconds if poll_seconds is not None
            else max(0.1, min(_DEFAULT_MAX_POLL_SECONDS, timeout_seconds / 4))
        )
        self._last_snooze: float = clock()
        self._stop: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None
        self._fired: bool = False
        """True once `on_expire` has been invoked — exposed as `fired` for tests."""

    @property
    def enabled(self) -> bool:
        """Whether this watchdog does anything at all (`timeout_seconds > 0`)."""
        return self._timeout > 0

    @property
    def fired(self) -> bool:
        return self._fired

    def start(self) -> None:
        """Start the watchdog thread, resetting the snoozed clock to now. A no-op when disabled
        (`timeout_seconds <= 0`) or already started."""
        if not self.enabled or self._thread is not None:
            return
        self._last_snooze = self._clock()
        self._thread = threading.Thread(
            target=self._run, name="klorb-liveness-watchdog", daemon=True)
        self._thread.start()

    def snooze(self) -> None:
        """Record that the event loop is alive as of now. Cheap enough to call on a short timer."""
        self._last_snooze = self._clock()

    def stop(self) -> None:
        """Disarm the watchdog (e.g. during an orderly shutdown) so it can't fire. Safe to call
        more than once, or when never started."""
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._poll):
            if self._clock() - self._last_snooze >= self._timeout:
                self._fired = True
                self._on_expire()
                return
