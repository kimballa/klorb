# © Copyright 2026 Aaron Kimball
"""Tests for klorb.agents.runtime: SubagentTracker bookkeeping, the subagent-output relay
formatting, and cascade_close_subagents."""
import threading
from collections.abc import Callable
from unittest.mock import MagicMock

from klorb.agents.runtime import (
    SubagentHandle,
    SubagentTracker,
    build_agent_group_interjection_provider,
    build_subagent_interjection_provider,
    cascade_close_subagents,
    find_session_in_group,
    total_active_subagents,
    walk_session_tree,
)
from klorb.session import Session, SessionConfig


def _handle(
    session: Session, role: str = "explorer", title: str = "task", *,
    parent_interested: bool = True,
) -> SubagentHandle:
    return SubagentHandle(
        session=session, thread=threading.Thread(target=lambda: None), cancel_event=threading.Event(),
        role=role, title=title, parent_interested=parent_interested)


def _child_session(
    parent: Session, make_session_config: Callable[..., SessionConfig],
) -> Session:
    return Session(make_session_config(role_name="explorer"), provider=MagicMock(), parent=parent)


def test_register_makes_handle_visible_via_handles(make_session_config: Callable[..., SessionConfig]) -> None:
    parent = Session(make_session_config(), provider=MagicMock())
    tracker = SubagentTracker()
    handle = _handle(_child_session(parent, make_session_config))

    tracker.register(handle)

    assert tracker.handles() == [handle]


def test_new_subagent_counts_toward_running_count(make_session_config: Callable[..., SessionConfig]) -> None:
    parent = Session(make_session_config(), provider=MagicMock())
    tracker = SubagentTracker()
    tracker.register(_handle(_child_session(parent, make_session_config)))

    assert tracker.running_count() == 1
    assert tracker.has_undelivered() is True


def test_mark_finished_drops_running_count_even_though_still_undelivered(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    """A finished-but-undelivered subagent is dormant, not concurrently processing."""
    parent = Session(make_session_config(), provider=MagicMock())
    tracker = SubagentTracker()
    handle = _handle(_child_session(parent, make_session_config))
    tracker.register(handle)

    tracker.mark_finished(handle.session.id, "the answer")

    assert handle.state == "finished"
    assert handle.output == "the answer"
    assert tracker.running_count() == 0
    assert tracker.has_undelivered() is True  # finished, but nobody has collected it yet


def test_pop_next_completed_marks_delivered(make_session_config: Callable[..., SessionConfig]) -> None:
    parent = Session(make_session_config(), provider=MagicMock())
    tracker = SubagentTracker()
    handle = _handle(_child_session(parent, make_session_config))
    tracker.register(handle)
    tracker.mark_finished(handle.session.id, "the answer")

    popped = tracker.pop_next_completed(timeout=1.0)

    assert popped is handle
    assert handle.delivered is True
    assert tracker.has_undelivered() is False


def test_pop_next_completed_times_out_when_nothing_is_ready() -> None:
    tracker = SubagentTracker()

    assert tracker.pop_next_completed(timeout=0.05) is None


def test_pop_next_completed_delivers_oldest_first(make_session_config: Callable[..., SessionConfig]) -> None:
    parent = Session(make_session_config(), provider=MagicMock())
    tracker = SubagentTracker()
    first = _handle(_child_session(parent, make_session_config), title="first")
    second = _handle(_child_session(parent, make_session_config), title="second")
    tracker.register(first)
    tracker.register(second)
    tracker.mark_finished(first.session.id, "1")
    tracker.mark_finished(second.session.id, "2")

    assert tracker.pop_next_completed(timeout=1.0) is first
    assert tracker.pop_next_completed(timeout=1.0) is second


def test_try_pop_completed_is_nonblocking_and_delivers_at_most_one(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    parent = Session(make_session_config(), provider=MagicMock())
    tracker = SubagentTracker()
    first = _handle(_child_session(parent, make_session_config), title="first")
    second = _handle(_child_session(parent, make_session_config), title="second")
    tracker.register(first)
    tracker.register(second)
    tracker.mark_finished(first.session.id, "1")
    tracker.mark_finished(second.session.id, "2")

    assert tracker.try_pop_completed() is first
    assert first.delivered is True
    assert second.delivered is False


def test_try_pop_completed_returns_none_when_nothing_is_ready() -> None:
    tracker = SubagentTracker()

    assert tracker.try_pop_completed() is None


def test_pop_all_completed_returns_empty_list_on_timeout() -> None:
    tracker = SubagentTracker()

    assert tracker.pop_all_completed(timeout=0.05) == []


def test_pop_all_completed_drains_every_completion_already_waiting(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    parent = Session(make_session_config(), provider=MagicMock())
    tracker = SubagentTracker()
    first = _handle(_child_session(parent, make_session_config), title="first")
    second = _handle(_child_session(parent, make_session_config), title="second")
    third = _handle(_child_session(parent, make_session_config), title="third")
    tracker.register(first)
    tracker.register(second)
    tracker.register(third)
    tracker.mark_finished(first.session.id, "1")
    tracker.mark_finished(second.session.id, "2")

    drained = tracker.pop_all_completed(timeout=1.0)

    assert drained == [first, second]
    assert first.delivered is True
    assert second.delivered is True
    assert third.delivered is False  # never finished -- not drained


def test_mark_finished_does_not_queue_an_uninterested_completion(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    """A subagent a human messaged directly (`parent_interested=False`) still records its own
    `state`/`output` on finishing, but must never be handed to `WaitForSubagent`."""
    parent = Session(make_session_config(), provider=MagicMock())
    tracker = SubagentTracker()
    handle = _handle(_child_session(parent, make_session_config), parent_interested=False)
    tracker.register(handle)

    tracker.mark_finished(handle.session.id, "the answer")

    assert handle.state == "finished"
    assert handle.output == "the answer"
    assert handle.delivered is False
    assert tracker.has_undelivered() is False  # nothing will ever pop this
    assert tracker.try_pop_completed() is None


def test_has_undelivered_ignores_uninterested_handles_even_alongside_an_interested_one(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    parent = Session(make_session_config(), provider=MagicMock())
    tracker = SubagentTracker()
    uninterested = _handle(_child_session(parent, make_session_config), title="uninterested",
        parent_interested=False)
    tracker.register(uninterested)
    tracker.mark_finished(uninterested.session.id, "human-addressed output")

    assert tracker.has_undelivered() is False  # the only handle is uninterested

    interested = _handle(_child_session(parent, make_session_config), title="interested")
    tracker.register(interested)
    tracker.mark_finished(interested.session.id, "parent-addressed output")

    assert tracker.has_undelivered() is True
    assert tracker.try_pop_completed() is interested


def test_completion_queue_survives_register_replacing_the_handle_dict_entry(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    """`register()` replaces `_handles[child_id]` on every dispatch, including while an older
    completion for that same id still sits in the queue. Popping must still
    return the handle that actually finished, not whatever now occupies the dict entry."""
    parent = Session(make_session_config(), provider=MagicMock())
    tracker = SubagentTracker()
    child = _child_session(parent, make_session_config)
    original = _handle(child, title="original")
    tracker.register(original)
    tracker.mark_finished(child.id, "original output")

    replacement = _handle(child, title="replacement")
    tracker.register(replacement)  # overwrites _handles[child.id]

    popped = tracker.pop_next_completed(timeout=1.0)

    assert popped is original
    assert popped.output == "original output"
    assert original.delivered is True
    assert replacement.delivered is False  # untouched -- its own turn hasn't finished


def test_cascade_close_subagents_does_not_relay_an_uninterested_handles_output(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    """A subagent a human messaged directly must not have its final output force-injected into
    the parent's persisted transcript at shutdown."""
    parent = Session(make_session_config(), provider=MagicMock())
    child = _child_session(parent, make_session_config)
    handle = _handle(child, role="explorer", title="find the bug", parent_interested=False)
    parent.subagent_tracker.register(handle)
    parent.subagent_tracker.mark_finished(child.id, "human-addressed output")
    message_count_before = len(parent.messages)

    cascade_close_subagents(parent)

    assert handle.delivered is False
    assert len(parent.messages) == message_count_before
    assert not any("human-addressed output" in m.content for m in parent.messages)


def test_build_subagent_interjection_provider_formats_id_role_title_and_output(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    parent = Session(make_session_config(), provider=MagicMock())
    tracker = SubagentTracker()
    child = _child_session(parent, make_session_config)
    handle = _handle(child, role="explorer", title="find the bug")
    tracker.register(handle)
    tracker.mark_finished(child.id, "found it in foo.py")

    provider = build_subagent_interjection_provider(tracker)
    body = provider()

    assert body is not None
    assert f"id: {child.id}" in body
    assert "role: explorer" in body
    assert "title: find the bug" in body
    assert "found it in foo.py" in body


def test_build_subagent_interjection_provider_returns_none_when_nothing_completed() -> None:
    tracker = SubagentTracker()

    assert build_subagent_interjection_provider(tracker)() is None


def test_total_active_subagents_counts_running_ones_across_whole_tree(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    root = Session(make_session_config(), provider=MagicMock())
    child = _child_session(root, make_session_config)
    grandchild = _child_session(child, make_session_config)
    root.subagent_tracker.register(_handle(child))
    child.subagent_tracker.register(_handle(grandchild))

    # Counted from any node in the tree -- walks up to the root first.
    assert total_active_subagents(root) == 2
    assert total_active_subagents(child) == 2
    assert total_active_subagents(grandchild) == 2


def test_total_active_subagents_excludes_finished_but_undelivered_ones(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    root = Session(make_session_config(), provider=MagicMock())
    child = _child_session(root, make_session_config)
    handle = _handle(child)
    root.subagent_tracker.register(handle)
    root.subagent_tracker.mark_finished(child.id, "done")

    assert total_active_subagents(root) == 0


def test_cascade_close_subagents_relays_undelivered_finished_output_into_parent_messages(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    parent = Session(make_session_config(), provider=MagicMock())
    child = _child_session(parent, make_session_config)
    handle = _handle(child, role="explorer", title="find the bug")
    parent.subagent_tracker.register(handle)
    parent.subagent_tracker.mark_finished(child.id, "found it in foo.py")

    cascade_close_subagents(parent)

    assert handle.delivered is True
    relayed = parent.messages[-1]
    assert relayed.role == "user"
    assert "found it in foo.py" in relayed.content
    assert 'subject="subagent"' in relayed.content


def test_cascade_close_subagents_terminates_a_still_running_subagent(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    parent = Session(make_session_config(), provider=MagicMock())
    child = _child_session(parent, make_session_config)
    started = threading.Event()
    cancel_event = threading.Event()

    def worker() -> None:
        started.set()
        cancel_event.wait(timeout=5.0)

    thread = threading.Thread(target=worker, daemon=True)
    handle = SubagentHandle(
        session=child, thread=thread, cancel_event=cancel_event, role="explorer", title="task")
    parent.subagent_tracker.register(handle)
    thread.start()
    assert started.wait(timeout=5.0)

    cascade_close_subagents(parent)

    assert cancel_event.is_set()
    assert handle.delivered is True
    relayed = parent.messages[-1]
    assert "harness closed before it finished" in relayed.content


def test_walk_session_tree_returns_only_the_root_when_it_has_no_subagents(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    root = Session(make_session_config(), provider=MagicMock())

    nodes = walk_session_tree(root)

    assert len(nodes) == 1
    assert nodes[0].session is root
    assert nodes[0].handle is None
    assert nodes[0].depth == 0


def test_walk_session_tree_is_a_pre_order_walk_in_creation_order(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    root = Session(make_session_config(), provider=MagicMock())
    first_child = _child_session(root, make_session_config)
    grandchild = _child_session(first_child, make_session_config)
    second_child = _child_session(root, make_session_config)
    first_handle = _handle(first_child, title="first")
    grandchild_handle = _handle(grandchild, title="grandchild")
    second_handle = _handle(second_child, title="second")
    root.subagent_tracker.register(first_handle)
    first_child.subagent_tracker.register(grandchild_handle)
    root.subagent_tracker.register(second_handle)

    nodes = walk_session_tree(root)

    assert [node.session for node in nodes] == [root, first_child, grandchild, second_child]
    assert [node.handle for node in nodes] == [None, first_handle, grandchild_handle, second_handle]
    assert [node.depth for node in nodes] == [0, 1, 2, 1]


def test_find_session_in_group_finds_the_root_itself(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    root = Session(make_session_config(), provider=MagicMock())

    assert find_session_in_group(root, root.id) is root


def test_find_session_in_group_finds_a_subagent_from_any_member_session(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    root = Session(make_session_config(), provider=MagicMock())
    child = _child_session(root, make_session_config)
    grandchild = _child_session(child, make_session_config)
    root.subagent_tracker.register(_handle(child))
    child.subagent_tracker.register(_handle(grandchild))

    assert find_session_in_group(root, grandchild.id) is grandchild
    assert find_session_in_group(child, grandchild.id) is grandchild
    assert find_session_in_group(grandchild, child.id) is child


def test_find_session_in_group_returns_none_for_an_unknown_id(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    root = Session(make_session_config(), provider=MagicMock())
    child = _child_session(root, make_session_config)
    root.subagent_tracker.register(_handle(child))

    assert find_session_in_group(root, "no-such-agent") is None


def test_cascade_close_subagents_recurses_into_grandchildren_first(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    parent = Session(make_session_config(), provider=MagicMock())
    child = _child_session(parent, make_session_config)
    grandchild = _child_session(child, make_session_config)
    grandchild_handle = _handle(grandchild)
    child.subagent_tracker.register(grandchild_handle)
    child.subagent_tracker.mark_finished(grandchild.id, "grandchild output")
    child_handle = _handle(child)
    parent.subagent_tracker.register(child_handle)
    parent.subagent_tracker.mark_finished(child.id, "child output")

    cascade_close_subagents(parent)

    assert grandchild_handle.delivered is True
    assert any("grandchild output" in m.content for m in child.messages)
    assert child_handle.delivered is True
    assert any("child output" in m.content for m in parent.messages)


def test_agent_group_provider_emits_table_on_first_call(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    root = Session(make_session_config(role_name="operator"), provider=MagicMock())
    provider = build_agent_group_interjection_provider(root)

    result = provider()

    assert result is not None
    assert "| Role | Id | Title | State |" in result
    assert "| operator |" in result
    assert "| running |" in result


def test_agent_group_provider_returns_none_when_group_unchanged(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    root = Session(make_session_config(role_name="operator"), provider=MagicMock())
    provider = build_agent_group_interjection_provider(root)
    provider()  # first call emits

    result = provider()  # second call, no change

    assert result is None


def test_agent_group_provider_emits_update_when_subagent_added(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    root = Session(make_session_config(role_name="operator"), provider=MagicMock())
    child = _child_session(root, make_session_config)
    provider = build_agent_group_interjection_provider(root)
    provider()  # baseline

    root.subagent_tracker.register(_handle(child))
    result = provider()

    assert result is not None
    assert child.id in result
    assert "| explorer |" in result


def test_agent_group_provider_emits_update_when_subagent_state_changes(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    root = Session(make_session_config(role_name="operator"), provider=MagicMock())
    child = _child_session(root, make_session_config)
    handle = _handle(child)
    root.subagent_tracker.register(handle)
    provider = build_agent_group_interjection_provider(root)
    provider()  # baseline with running child

    root.subagent_tracker.mark_finished(child.id, "done")
    result = provider()

    assert result is not None
    assert "| finished |" in result


def test_agent_group_provider_works_for_subagent_session(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    root = Session(make_session_config(role_name="operator"), provider=MagicMock())
    child = _child_session(root, make_session_config)
    root.subagent_tracker.register(_handle(child))
    provider = build_agent_group_interjection_provider(child)

    result = provider()

    assert result is not None
    assert root.id in result
    assert child.id in result


def test_agent_group_provider_tracks_nested_subagents(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    root = Session(make_session_config(role_name="operator"), provider=MagicMock())
    child = _child_session(root, make_session_config)
    grandchild = _child_session(child, make_session_config)
    root.subagent_tracker.register(_handle(child))
    child.subagent_tracker.register(_handle(grandchild))
    provider = build_agent_group_interjection_provider(root)

    result = provider()

    assert result is not None
    assert grandchild.id in result
    # root + child + grandchild = 3 rows + header + separator = 5 lines
    assert len(result.split("\n")) == 5
