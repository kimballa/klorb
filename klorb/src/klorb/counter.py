# © Copyright 2026 Aaron Kimball
"""`AtomicCounter`: a lock-guarded integer counter safe for concurrent read-modify-write."""

import threading


class AtomicCounter:
    """A thread-safe integer counter: every read, increment, and decrement takes an internal
    lock so concurrent callers never race on the same read-modify-write."""

    def __init__(self, initial: int = 0) -> None:
        self._value = initial
        self._lock = threading.Lock()

    def increment(self, step: int = 1) -> int:
        """Add `step` to the counter and return the new value."""
        with self._lock:
            self._value += step
            return self._value

    def decrement(self, step: int = 1) -> int:
        """Subtract `step` from the counter and return the new value."""
        with self._lock:
            self._value -= step
            return self._value

    def get_value(self) -> int:
        """Return the counter's current value."""
        with self._lock:
            return self._value

    def __repr__(self) -> str:
        return f"AtomicCounter({self.get_value()})"

    def __str__(self) -> str:
        return str(self.get_value())
