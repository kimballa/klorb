# © Copyright 2026 Aaron Kimball
"""Tests for `klorb.server.plan_updates.build_plan_update` -- pure chainlink-issue-to-ACP-`plan`
mapping. See docs/specs/klorb-server.md's "Chainlink task-plan updates" section."""

from typing import Any

from klorb.server.plan_updates import build_plan_update


def _issue(
    issue_id: int, title: str = "Task", *, status: str = "open", priority: str = "medium",
    blocked_by: list[int] | None = None, blocked_by_open: list[int] | None = None,
) -> dict[str, Any]:
    return {
        "id": issue_id, "title": title, "status": status, "priority": priority,
        "blocked_by": blocked_by or [], "blocked_by_open": blocked_by_open or [],
    }


def test_open_issue_maps_to_pending() -> None:
    update = build_plan_update([_issue(1, "Write the spec")], cur_task_id=None)

    assert len(update.entries) == 1
    entry = update.entries[0]
    assert entry.content == "#1 Write the spec"
    assert entry.status == "pending"
    assert entry.field_meta is not None
    assert entry.field_meta["klorb"] == {
        "issueId": 1, "openBlockerCount": 0, "blockedBy": [], "closed": False,
        "isCurrentTask": False,
    }


def test_closed_issue_maps_to_completed_even_when_it_was_the_current_task() -> None:
    update = build_plan_update([_issue(1, status="closed")], cur_task_id=1)

    entry = update.entries[0]
    assert entry.status == "completed"
    assert entry.field_meta is not None
    assert entry.field_meta["klorb"]["closed"] is True
    assert entry.field_meta["klorb"]["isCurrentTask"] is True


def test_current_task_maps_to_in_progress() -> None:
    update = build_plan_update([_issue(1)], cur_task_id=1)

    entry = update.entries[0]
    assert entry.status == "in_progress"
    assert entry.field_meta is not None
    assert entry.field_meta["klorb"]["isCurrentTask"] is True


def test_blocked_by_an_open_issue_counts_as_an_open_blocker() -> None:
    update = build_plan_update(
        [_issue(1), _issue(2, blocked_by=[1], blocked_by_open=[1])], cur_task_id=None)

    blocked_entry = update.entries[1]
    assert blocked_entry.field_meta is not None
    assert blocked_entry.field_meta["klorb"]["openBlockerCount"] == 1
    assert blocked_entry.field_meta["klorb"]["blockedBy"] == [1]


def test_blocked_by_a_closed_issue_does_not_count_as_an_open_blocker() -> None:
    update = build_plan_update(
        [_issue(1, status="closed"), _issue(2, blocked_by=[1])], cur_task_id=None)

    blocked_entry = update.entries[1]
    assert blocked_entry.field_meta is not None
    assert blocked_entry.field_meta["klorb"]["openBlockerCount"] == 0


def test_priority_collapse() -> None:
    update = build_plan_update(
        [
            _issue(1, priority="critical"), _issue(2, priority="high"),
            _issue(3, priority="medium"), _issue(4, priority="low"),
        ],
        cur_task_id=None,
    )

    assert [entry.priority for entry in update.entries] == ["high", "high", "medium", "low"]


def test_update_level_meta_counts() -> None:
    update = build_plan_update(
        [
            _issue(1),  # open, unblocked, current
            _issue(2, blocked_by=[1], blocked_by_open=[1]),  # open, blocked
            _issue(3, status="closed"),  # closed
        ],
        cur_task_id=1,
    )

    assert update.field_meta is not None
    assert update.field_meta["klorb"] == {
        "openCount": 2, "closedCount": 1, "blockedCount": 1, "currentTaskId": 1,
    }


def test_ordering_preserved_from_input() -> None:
    issues = [_issue(3), _issue(1), _issue(2)]

    update = build_plan_update(issues, cur_task_id=None)

    issue_ids = [
        entry.field_meta["klorb"]["issueId"] for entry in update.entries  # type: ignore[index]
    ]
    assert issue_ids == [3, 1, 2]


def test_no_current_task_marks_nothing_in_progress() -> None:
    update = build_plan_update([_issue(1), _issue(2)], cur_task_id=None)

    assert [entry.status for entry in update.entries] == ["pending", "pending"]
