# © Copyright 2026 Aaron Kimball
"""Unit tests for `klorb.counter.AtomicCounter`."""

import threading

from klorb.counter import AtomicCounter


def test_increment_returns_new_value() -> None:
    counter = AtomicCounter()
    assert counter.increment() == 1
    assert counter.increment() == 2
    assert counter.increment(step=3) == 5


def test_decrement_returns_new_value() -> None:
    counter = AtomicCounter(initial=5)
    assert counter.decrement() == 4
    assert counter.decrement(step=2) == 2


def test_get_value_reflects_current_state() -> None:
    counter = AtomicCounter(initial=10)
    assert counter.get_value() == 10
    counter.increment()
    assert counter.get_value() == 11


def test_concurrent_increments_are_not_lost() -> None:
    counter = AtomicCounter()
    barrier = threading.Barrier(20)

    def bump() -> None:
        barrier.wait(timeout=5.0)
        for _ in range(100):
            counter.increment()

    workers = [threading.Thread(target=bump) for _ in range(20)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5.0)

    assert counter.get_value() == 2000
