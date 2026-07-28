# © Copyright 2026 Aaron Kimball
"""Tests for klorb.lockfile."""

import time
from pathlib import Path
from unittest.mock import patch

from klorb.lockfile import acquire_lockfile_with_backoff, create_lockfile


def test_try_acquire_succeeds_on_an_unlocked_path(tmp_path: Path) -> None:
    lock = create_lockfile(tmp_path / "foo.lock")
    assert lock.try_acquire()
    lock.release()


def test_try_acquire_is_idempotent_for_the_same_instance(tmp_path: Path) -> None:
    lock = create_lockfile(tmp_path / "foo.lock")
    assert lock.try_acquire()
    assert lock.try_acquire()
    lock.release()


def test_try_acquire_fails_when_another_holder_has_it(tmp_path: Path) -> None:
    path = tmp_path / "foo.lock"
    holder = create_lockfile(path)
    assert holder.try_acquire()
    try:
        contender = create_lockfile(path)
        assert not contender.try_acquire()
    finally:
        holder.release()


def test_try_acquire_succeeds_after_the_holder_releases(tmp_path: Path) -> None:
    path = tmp_path / "foo.lock"
    holder = create_lockfile(path)
    assert holder.try_acquire()
    holder.release()

    contender = create_lockfile(path)
    assert contender.try_acquire()
    contender.release()


def test_release_is_a_no_op_when_not_held(tmp_path: Path) -> None:
    lock = create_lockfile(tmp_path / "foo.lock")
    lock.release()  # must not raise


def test_creates_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "foo.lock"
    lock = create_lockfile(path)
    assert lock.try_acquire()
    lock.release()
    assert path.is_file()


def test_is_held_by_other_false_for_missing_file(tmp_path: Path) -> None:
    lock = create_lockfile(tmp_path / "foo.lock")
    assert not lock.is_held_by_other()


def test_is_held_by_other_false_when_this_instance_holds_it(tmp_path: Path) -> None:
    path = tmp_path / "foo.lock"
    lock = create_lockfile(path)
    assert lock.try_acquire()
    try:
        assert not lock.is_held_by_other()
    finally:
        lock.release()


def test_is_held_by_other_true_when_a_different_process_holds_it(tmp_path: Path) -> None:
    path = tmp_path / "foo.lock"
    holder = create_lockfile(path)
    assert holder.try_acquire()
    try:
        prober = create_lockfile(path)
        assert prober.is_held_by_other()
    finally:
        holder.release()


def test_is_held_by_other_false_after_release(tmp_path: Path) -> None:
    path = tmp_path / "foo.lock"
    holder = create_lockfile(path)
    assert holder.try_acquire()
    holder.release()

    prober = create_lockfile(path)
    assert not prober.is_held_by_other()


class TestAcquireLockfileWithBackoff:
    def test_returns_lock_immediately_when_uncontended(self, tmp_path: Path) -> None:
        path = tmp_path / "foo.lock"
        started = time.monotonic()
        lock = acquire_lockfile_with_backoff(path, max_attempts=4, initial_delay_seconds=0.05)
        elapsed = time.monotonic() - started

        assert lock is not None
        assert elapsed < 0.05
        lock.release()

    def test_returns_none_after_exhausting_attempts(self, tmp_path: Path) -> None:
        path = tmp_path / "foo.lock"
        holder = create_lockfile(path)
        assert holder.try_acquire()
        try:
            with patch("time.sleep") as mock_sleep:
                result = acquire_lockfile_with_backoff(path, max_attempts=3, initial_delay_seconds=0.01)
            assert result is None
            assert mock_sleep.call_count == 2
            delays = [call.args[0] for call in mock_sleep.call_args_list]
            assert delays == [0.01, 0.02]
        finally:
            holder.release()

    def test_succeeds_once_contention_clears_mid_retry(self, tmp_path: Path) -> None:
        path = tmp_path / "foo.lock"
        holder = create_lockfile(path)
        assert holder.try_acquire()

        release_after = {"calls": 0}
        real_sleep = time.sleep

        def fake_sleep(delay: float) -> None:
            release_after["calls"] += 1
            if release_after["calls"] == 1:
                holder.release()
            real_sleep(0)

        with patch("time.sleep", side_effect=fake_sleep):
            result = acquire_lockfile_with_backoff(path, max_attempts=4, initial_delay_seconds=0.001)

        assert result is not None
        result.release()
