# © Copyright 2026 Aaron Kimball
"""Tests for klorb.hooks.timer_events."""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

import pytest

from klorb.hooks.config import MIN_EVENT_DEBOUNCE_SECONDS, HookConfig, TimerEventConfig
from klorb.hooks.hook_api import EventInput
from klorb.hooks.timer_events import TimerScheduler, clamp_timer_intervals, compute_next_fire

_SHORT_INTERVAL_MINUTES = 0.002
"""~120ms."""


@pytest.fixture
def _fast_debounce_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_schedule_next`'s own `MIN_EVENT_DEBOUNCE_SECONDS` delay floor would otherwise force every
    real-timer test below to wait a genuine 10 seconds per fire."""
    monkeypatch.setattr("klorb.hooks.timer_events.MIN_EVENT_DEBOUNCE_SECONDS", _SHORT_INTERVAL_MINUTES * 60)


def _entry(*, interval_minutes: float | None = None, cron: str | None = None) -> TimerEventConfig:
    return TimerEventConfig(
        action=HookConfig(type="chat", prompt="tick"), interval_minutes=interval_minutes, cron=cron)


def _no_op_dispatch(entries: list[TimerEventConfig], event_input: EventInput) -> None:
    pass


# --- compute_next_fire ---


def test_compute_next_fire_for_an_interval_entry() -> None:
    after = datetime(2026, 1, 1, 0, 0, 0)
    entry = _entry(interval_minutes=5)
    assert compute_next_fire(entry, after) == datetime(2026, 1, 1, 0, 5, 0)


def test_compute_next_fire_for_a_cron_entry() -> None:
    after = datetime(2026, 8, 8, 12, 0, 0)
    entry = _entry(cron="30 2,4,6,8 * * *")
    assert compute_next_fire(entry, after) == datetime(2026, 8, 9, 2, 30, 0)


def test_compute_next_fire_raises_for_an_entry_with_neither_cron_nor_interval() -> None:
    with pytest.raises(ValueError, match="neither"):
        compute_next_fire(_entry(), datetime.now())


def test_compute_next_fire_raises_for_an_invalid_cron_string() -> None:
    with pytest.raises(ValueError, match="column"):
        compute_next_fire(_entry(cron="not a cron"), datetime.now())


# --- clamp_timer_intervals ---


def test_clamp_timer_intervals_clamps_a_tighter_interval_with_a_warning() -> None:
    entry = _entry(interval_minutes=0.01)
    warnings: list[str] = []
    clamp_timer_intervals([entry], source_label="test", warnings=warnings)
    assert entry.interval_minutes == pytest.approx(10.0 / 60.0)
    assert len(warnings) == 1
    assert "test" in warnings[0]


def test_clamp_timer_intervals_leaves_an_interval_at_or_above_the_floor_untouched() -> None:
    entry = _entry(interval_minutes=1.0)
    warnings: list[str] = []
    clamp_timer_intervals([entry], source_label="test", warnings=warnings)
    assert entry.interval_minutes == 1.0
    assert warnings == []


def test_clamp_timer_intervals_leaves_a_cron_entry_untouched() -> None:
    entry = _entry(cron="* * * * *")
    warnings: list[str] = []
    clamp_timer_intervals([entry], source_label="test", warnings=warnings)
    assert entry.cron == "* * * * *"
    assert entry.interval_minutes is None
    assert warnings == []


def test_clamp_timer_intervals_is_idempotent() -> None:
    entry = _entry(interval_minutes=0.01)
    warnings: list[str] = []
    clamp_timer_intervals([entry], source_label="test", warnings=warnings)
    clamp_timer_intervals([entry], source_label="test", warnings=warnings)
    assert len(warnings) == 1


# --- TimerScheduler ---


class _Recorder:
    """Captures every `dispatch` call, signaling an `asyncio.Event` so a test can `await` the
    next background-thread delivery instead of polling or sleeping a fixed duration."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[TimerEventConfig], EventInput]] = []
        self.event = asyncio.Event()
        self._loop = asyncio.get_running_loop()

    def __call__(self, entries: list[TimerEventConfig], event_input: EventInput) -> None:
        self.calls.append((entries, event_input))
        self._loop.call_soon_threadsafe(self.event.set)

    async def wait(self) -> None:
        await asyncio.wait_for(self.event.wait(), timeout=5.0)
        self.event.clear()


@pytest.fixture
async def recorder() -> _Recorder:
    """An async fixture so `_Recorder.__init__` captures the test's own running event loop."""
    return _Recorder()


@pytest.mark.usefixtures("_fast_debounce_floor")
async def test_timer_scheduler_fires_the_action_on_its_interval(
    tmp_path: Path, recorder: _Recorder,
) -> None:
    entry = _entry(interval_minutes=_SHORT_INTERVAL_MINUTES)
    scheduler = TimerScheduler(tmp_path, [entry], dispatch=recorder)
    scheduler.start()
    try:
        await recorder.wait()
    finally:
        scheduler.close()

    assert len(recorder.calls) == 1
    entries, event_input = recorder.calls[0]
    assert entries == [entry]
    assert event_input.hook == "Timer"
    assert event_input.workspace_root == str(tmp_path)


@pytest.mark.usefixtures("_fast_debounce_floor")
async def test_timer_scheduler_reschedules_and_fires_again(
    tmp_path: Path, recorder: _Recorder,
) -> None:
    entry = _entry(interval_minutes=_SHORT_INTERVAL_MINUTES)
    scheduler = TimerScheduler(tmp_path, [entry], dispatch=recorder)
    scheduler.start()
    try:
        await recorder.wait()
        await recorder.wait()
    finally:
        scheduler.close()

    assert len(recorder.calls) == 2


@pytest.mark.usefixtures("_fast_debounce_floor")
async def test_timer_scheduler_dispatches_each_entry_independently(
    tmp_path: Path, recorder: _Recorder,
) -> None:
    fast = _entry(interval_minutes=_SHORT_INTERVAL_MINUTES)
    slow = _entry(interval_minutes=_SHORT_INTERVAL_MINUTES * 50)
    scheduler = TimerScheduler(tmp_path, [fast, slow], dispatch=recorder)
    scheduler.start()
    try:
        await recorder.wait()
    finally:
        scheduler.close()

    assert len(recorder.calls) == 1
    entries, _ = recorder.calls[0]
    assert entries == [fast]


@pytest.mark.usefixtures("_fast_debounce_floor")
async def test_timer_scheduler_reschedules_after_dispatch_raises(
    tmp_path: Path, recorder: _Recorder, caplog: pytest.LogCaptureFixture,
) -> None:
    """A dispatch failure (a hook handler error, or `Session.deliver_event_message` raising
    `ChainedHookMessageUndeliverableError` while idle) must not silently stop this entry from
    ever firing again."""
    call_count = 0

    def _raise_once_then_record(
        entries: list[TimerEventConfig], event_input: EventInput,
    ) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        recorder(entries, event_input)

    entry = _entry(interval_minutes=_SHORT_INTERVAL_MINUTES)
    scheduler = TimerScheduler(tmp_path, [entry], dispatch=_raise_once_then_record)
    with caplog.at_level(logging.ERROR, logger="klorb.hooks.timer_events"):
        scheduler.start()
        try:
            await recorder.wait()
        finally:
            scheduler.close()

    assert call_count == 2
    assert len(recorder.calls) == 1
    assert "raised while dispatching" in caplog.text


def test_schedule_next_clamps_delay_to_the_debounce_floor(tmp_path: Path) -> None:
    """`_schedule_next`'s own delay is clamped to `MIN_EVENT_DEBOUNCE_SECONDS`, not just
    `clamp_timer_intervals`'s config-load-time floor on `interval_minutes`."""
    entry = _entry(interval_minutes=_SHORT_INTERVAL_MINUTES)
    scheduler = TimerScheduler(tmp_path, [entry], dispatch=_no_op_dispatch)
    try:
        scheduler.start()
        assert scheduler._timers[0].interval == pytest.approx(MIN_EVENT_DEBOUNCE_SECONDS)
    finally:
        scheduler.close()


def test_timer_scheduler_skips_an_entry_with_no_cron_or_interval(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    entry = _entry()
    scheduler = TimerScheduler(tmp_path, [entry], dispatch=_no_op_dispatch)
    with caplog.at_level(logging.WARNING, logger="klorb.hooks.timer_events"):
        scheduler.start()
    try:
        assert scheduler._timers == {}
    finally:
        scheduler.close()
    assert "invalid schedule" in caplog.text


def test_start_is_a_no_op_with_no_entries(tmp_path: Path) -> None:
    scheduler = TimerScheduler(tmp_path, [], dispatch=_no_op_dispatch)
    scheduler.start()
    try:
        assert scheduler._timers == {}
    finally:
        scheduler.close()


def test_close_is_safe_to_call_more_than_once(tmp_path: Path) -> None:
    entry = _entry(interval_minutes=_SHORT_INTERVAL_MINUTES)
    scheduler = TimerScheduler(tmp_path, [entry], dispatch=_no_op_dispatch)
    scheduler.start()
    scheduler.close()
    scheduler.close()


# --- one-shot behavior ---


@pytest.mark.usefixtures("_fast_debounce_floor")
async def test_one_shot_timer_fires_once_then_stops(
    tmp_path: Path, recorder: _Recorder,
) -> None:
    entry = _entry(interval_minutes=_SHORT_INTERVAL_MINUTES)
    entry.one_shot = True
    scheduler = TimerScheduler(tmp_path, [entry], dispatch=recorder)
    scheduler.start()
    try:
        await recorder.wait()
        # Give a bit of extra time to confirm no second fire.
        await asyncio.sleep(_SHORT_INTERVAL_MINUTES * 60 * 3)
    finally:
        scheduler.close()

    assert len(recorder.calls) == 1


@pytest.mark.usefixtures("_fast_debounce_floor")
async def test_recurring_timer_still_reschedules_after_one_shot_sibling_fires(
    tmp_path: Path, recorder: _Recorder,
) -> None:
    one_shot = _entry(interval_minutes=_SHORT_INTERVAL_MINUTES)
    one_shot.one_shot = True
    recurring = _entry(interval_minutes=_SHORT_INTERVAL_MINUTES)
    scheduler = TimerScheduler(tmp_path, [one_shot, recurring], dispatch=recorder)
    scheduler.start()
    try:
        # Wait for at least 3 fires total -- one from one-shot, rest from recurring.
        await recorder.wait()
        await recorder.wait()
        await recorder.wait()
    finally:
        scheduler.close()

    # The recurring entry fires at least twice; the one-shot fires exactly once.
    assert len(recorder.calls) >= 3
