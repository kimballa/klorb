# © Copyright 2026 Aaron Kimball
"""`TimerScheduler`: the runtime counterpart to `klorb.hooks.config.TimerEventConfig` -- fires
each configured entry's `action` on its own `interval_minutes`/`cron` schedule, best-effort only:
a fire time that elapses while no klorb process for this workspace is running is simply missed,
never queued or caught up on restart. Also home to `clamp_timer_intervals`, the config-load-time
floor enforcement for `interval_minutes` (see `klorb.hooks.config.MIN_EVENT_DEBOUNCE_SECONDS`).
"""

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from croniter import croniter

from klorb.hooks.config import MIN_EVENT_DEBOUNCE_SECONDS, TimerEventConfig
from klorb.hooks.hook_api import EventInput

logger = logging.getLogger(__name__)


def clamp_timer_intervals(
    entries: list[TimerEventConfig], *, source_label: str, warnings: list[str],
) -> None:
    """Clamp each of `entries`' `interval_minutes` up to `MIN_EVENT_DEBOUNCE_SECONDS`, in
    place, when it requests a tighter cadence -- collecting a message into `warnings` (see
    `ProcessConfig.config_warnings`) for each one clamped rather than crashing config load. A
    `cron`-scheduled entry needs no such clamp: standard five-field cron grammar can't express
    anything tighter than once a minute, already above the floor. Idempotent -- safe to call
    again over an already-clamped entry, which is how `load_process_config` re-applies it across
    every config layer without tracking which entries a given layer newly contributed.
    """
    floor_minutes = MIN_EVENT_DEBOUNCE_SECONDS / 60.0
    for entry in entries:
        if entry.interval_minutes is not None and entry.interval_minutes < floor_minutes:
            message = (
                f"{source_label}: Timer entry requests interval_minutes={entry.interval_minutes!r}, "
                f"tighter than the {MIN_EVENT_DEBOUNCE_SECONDS:g}-second floor; clamping to "
                f"{floor_minutes:g} minutes.")
            logger.warning(message)
            warnings.append(message)
            entry.interval_minutes = floor_minutes


def compute_next_fire(entry: TimerEventConfig, after: datetime) -> datetime:
    """The next moment `entry`'s `action` should fire, strictly after `after`: `after +
    interval_minutes` for an interval entry, or `cron`'s next scheduled moment (via `croniter`)
    for a cron entry. A pure function, deliberately kept apart from `TimerScheduler`'s own
    background-thread bookkeeping, so cron/interval math is unit-testable without waiting on a
    real clock. Raises `ValueError` for a malformed `cron` string (via `croniter`), or an entry
    with neither `cron` nor `interval_minutes` set."""
    if entry.cron is not None:
        return croniter(entry.cron, after).get_next(datetime)
    if entry.interval_minutes is not None:
        return after + timedelta(minutes=entry.interval_minutes)
    raise ValueError("Timer entry has neither cron nor interval_minutes set.")


class TimerScheduler:
    """Runs each of `entries`' own `action` on its own independent `interval_minutes`/`cron`
    schedule, via one self-rescheduling `threading.Timer` per entry -- unlike
    `klorb.hooks.fs_events.FileSystemWatcher`, entries here have no shared debounce window to
    batch behind, so `dispatch` is called once per entry, each time it fires, as a single-entry
    list. `start()`/`close()` are idempotent.
    """

    def __init__(
        self, workspace_root: Path, entries: list[TimerEventConfig], *,
        dispatch: Callable[[list[TimerEventConfig], EventInput], None],
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._workspace_root = workspace_root
        self._entries = entries
        self._dispatch = dispatch
        self._now = now
        self._timers: dict[int, threading.Timer] = {}
        self._closing_event = threading.Event()

    def start(self) -> None:
        """Schedule every configured entry's first fire. A no-op if already started, already
        closed, or `entries` is empty."""
        if self._timers or self._closing_event.is_set() or not self._entries:
            return
        for index, entry in enumerate(self._entries):
            self._schedule_next(index, entry)
        logger.debug(
            "TimerScheduler scheduled %d entr(y/ies) under %s", len(self._entries), self._workspace_root)

    def close(self) -> None:
        """Cancel every pending timer. Safe to call more than once, or when never started."""
        self._closing_event.set()
        for timer in self._timers.values():
            timer.cancel()
        self._timers = {}
        logger.debug("TimerScheduler stopped scheduling under %s", self._workspace_root)

    def _schedule_next(self, index: int, entry: TimerEventConfig) -> None:
        if self._closing_event.is_set():
            return
        now = self._now()
        try:
            next_fire = compute_next_fire(entry, now)
        except ValueError as exc:
            logger.warning(
                "TimerScheduler entry %r has an invalid schedule (%s); not scheduling.",
                entry.action.name, exc)
            return
        delay = max(MIN_EVENT_DEBOUNCE_SECONDS, (next_fire - now).total_seconds())
        timer = threading.Timer(delay, self._fire, args=(index, entry))
        timer.daemon = True
        self._timers[index] = timer
        timer.start()

    def _fire(self, index: int, entry: TimerEventConfig) -> None:
        if self._closing_event.is_set():
            return
        try:
            self._dispatch(
                [entry], EventInput(hook="Timer", workspace_root=str(self._workspace_root)))
        except Exception:
            # `_schedule_next` below is what re-arms this entry's next fire -- an uncaught
            # exception here (a hook handler failure, or `Session.deliver_event_message` raising
            # `ChainedHookMessageUndeliverableError` when the session is idle) would otherwise
            # skip it and silently stop this entry from ever firing again.
            logger.error(
                "Timer entry %r raised while dispatching; still rescheduling.",
                entry.action.name, exc_info=True)
        self._schedule_next(index, entry)
