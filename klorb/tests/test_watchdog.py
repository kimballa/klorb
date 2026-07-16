# © Copyright 2026 Aaron Kimball
"""Unit tests for `klorb.watchdog` — the `LivenessWatchdog` daemon and the `run_bounded_cleanup`
half of `force_exit`. `force_exit` itself is not exercised directly (it calls `os._exit`, which
would terminate the test process); its behaviour is covered by testing `run_bounded_cleanup` (the
time-boxing) and `LivenessWatchdog` (the trigger) separately.
"""

import threading
import time
from unittest.mock import MagicMock

from klorb.watchdog import LivenessWatchdog, run_bounded_cleanup


def test_watchdog_fires_on_expire_when_not_snoozed() -> None:
    fired = threading.Event()
    watchdog = LivenessWatchdog(0.05, fired.set, poll_seconds=0.01)
    watchdog.start()
    assert fired.wait(2.0), "watchdog never fired despite no snoozes"
    assert watchdog.fired is True


def test_watchdog_does_not_fire_while_snoozed() -> None:
    fired = threading.Event()
    watchdog = LivenessWatchdog(0.2, fired.set, poll_seconds=0.01)
    watchdog.start()
    # Snooze more often (every 0.02s) than the 0.2s timeout for well over a full timeout window.
    for _ in range(20):
        watchdog.snooze()
        time.sleep(0.02)
    watchdog.stop()
    assert not fired.is_set()


def test_watchdog_stop_before_lapse_prevents_fire() -> None:
    fired = threading.Event()
    watchdog = LivenessWatchdog(0.05, fired.set, poll_seconds=0.01)
    watchdog.stop()
    watchdog.start()
    assert not fired.wait(0.3)
    assert watchdog.fired is False


def test_watchdog_disabled_when_timeout_not_positive() -> None:
    on_expire = MagicMock()
    watchdog = LivenessWatchdog(0, on_expire)
    assert watchdog.enabled is False
    watchdog.start()  # no-op
    time.sleep(0.1)
    on_expire.assert_not_called()
    assert watchdog.fired is False


def test_run_bounded_cleanup_runs_cleanup() -> None:
    ran = threading.Event()
    run_bounded_cleanup(ran.set, 1.0)
    assert ran.is_set()


def test_run_bounded_cleanup_bounds_a_slow_cleanup() -> None:
    started = threading.Event()

    def slow() -> None:
        started.set()
        time.sleep(5.0)

    began = time.monotonic()
    run_bounded_cleanup(slow, 0.1)
    elapsed = time.monotonic() - began
    assert started.wait(1.0), "cleanup never started"
    assert elapsed < 2.0, f"run_bounded_cleanup blocked for {elapsed:.2f}s past its grace"


def test_run_bounded_cleanup_swallows_cleanup_exception() -> None:
    def boom() -> None:
        raise ValueError("cleanup blew up")

    # Must return normally rather than propagate: the caller is on its way to os._exit.
    run_bounded_cleanup(boom, 1.0)
