# © Copyright 2026 Aaron Kimball
"""Tests for klorb.agents.messaging.AgentMessageQueue."""
import pytest

from klorb.agents.messaging import AgentMessageQueue
from klorb.tools.exceptions import ToolCallError


def test_enqueue_and_pop_all_for_preserves_send_order() -> None:
    queue = AgentMessageQueue()
    queue.enqueue("a", "explorer", "target", "first")
    queue.enqueue("b", "reviewer", "other", "unrelated")
    queue.enqueue("c", "planner", "target", "second")

    popped = queue.pop_all_for("target")
    assert [m.body for m in popped] == ["first", "second"]
    assert not queue.has_pending("target")
    assert queue.has_pending("other")


def test_has_pending_is_non_destructive() -> None:
    queue = AgentMessageQueue()
    queue.enqueue("a", "explorer", "target", "hi")

    assert queue.has_pending("target")
    assert queue.has_pending("target")  # calling it again doesn't consume anything
    assert queue.pop_all_for("target")[0].body == "hi"


def test_enqueue_rejects_once_the_queue_is_full() -> None:
    queue = AgentMessageQueue(max_size=2)
    queue.enqueue("a", "explorer", "x", "1")
    queue.enqueue("a", "explorer", "y", "2")

    with pytest.raises(ToolCallError) as exc_info:
        queue.enqueue("a", "explorer", "z", "3")
    assert exc_info.value.category == "transient"


def test_peek_next_dormant_candidate_skips_non_dormant_recipients_without_stopping() -> None:
    queue = AgentMessageQueue()
    queue.enqueue("a", "explorer", "running-target", "hi")
    queue.enqueue("a", "explorer", "dormant-target", "hi")

    dormant = {"dormant-target"}
    candidate = queue.peek_next_dormant_candidate(lambda rid: rid in dormant)
    assert candidate == "dormant-target"


def test_peek_next_dormant_candidate_returns_none_when_nothing_is_dormant() -> None:
    queue = AgentMessageQueue()
    queue.enqueue("a", "explorer", "running-target", "hi")

    assert queue.peek_next_dormant_candidate(lambda rid: False) is None


def test_peek_next_dormant_candidate_considers_each_recipient_once() -> None:
    queue = AgentMessageQueue()
    queue.enqueue("a", "explorer", "target", "first")
    queue.enqueue("b", "reviewer", "target", "second")

    calls: list[str] = []

    def is_dormant(recipient_id: str) -> bool:
        calls.append(recipient_id)
        return True

    assert queue.peek_next_dormant_candidate(is_dormant) == "target"
    assert calls == ["target"]
