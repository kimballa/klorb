# © Copyright 2026 Aaron Kimball
"""Pure functions mapping a session's chainlink issues onto an ACP `plan` session update -- see
docs/specs/klorb-server.md's "Chainlink task-plan updates" section. Kept free of I/O: the
chainlink fetch itself (`ChainlinkClient`/`fetch_and_sort_issues`) stays in
`klorb.server.turn_bridge.TurnBridge`, which passes the already-fetched, already-sorted issue
list to `build_plan_update` here.
"""

from typing import Any

from acp.schema import AgentPlanUpdate, PlanEntry, PlanEntryPriority, PlanEntryStatus

from klorb.tools.tasks.common import open_blocker_count

_ACP_PRIORITY_MAP: dict[str, PlanEntryPriority] = {
    "critical": "high",
    "high": "high",
    "medium": "medium",
    "low": "low",
}
"""Collapses chainlink's four-level `klorb.tools.tasks.common.PRIORITY_ORDER` onto ACP's
three-level `PlanEntryPriority` -- `critical` and `high` both become `"high"`, since ACP has no
finer grade above it. Recorded here (rather than derived) since the collapse itself, not just
the source priority set, is the durable contract a client can depend on."""


def build_plan_update(issues: list[dict[str, Any]], cur_task_id: int | None) -> AgentPlanUpdate:
    """Map `issues` -- already fetched and sorted by `klorb.tools.tasks.common.
    fetch_and_sort_issues` (this function never re-sorts) -- onto an ACP `plan` session update:
    one `PlanEntry` per issue, in the same order, plus `_meta.klorb` detail at both the entry and
    update level so a klorb-aware client can render the task panel without re-deriving it (see
    docs/specs/klorb-server.md)."""
    open_ids = {issue["id"] for issue in issues if issue.get("status") == "open"}
    entries: list[PlanEntry] = []
    open_count = 0
    closed_count = 0
    blocked_count = 0
    for issue in issues:
        issue_id = issue.get("id")
        closed = issue.get("status") != "open"
        if closed:
            closed_count += 1
        else:
            open_count += 1
        blocker_count = open_blocker_count(issue, open_ids)
        if not closed and blocker_count > 0:
            blocked_count += 1
        is_current = cur_task_id is not None and issue_id == cur_task_id
        status: PlanEntryStatus = (
            "completed" if closed else "in_progress" if is_current else "pending")
        priority = _ACP_PRIORITY_MAP.get(issue.get("priority", "medium"), "medium")
        entries.append(PlanEntry(
            content=f"#{issue_id} {issue.get('title', '')}",
            priority=priority,
            status=status,
            field_meta={"klorb": {
                "issueId": issue_id,
                "openBlockerCount": blocker_count,
                "blockedBy": list(issue.get("blocked_by", [])),
                "closed": closed,
                "isCurrentTask": is_current,
            }},
        ))
    return AgentPlanUpdate(
        session_update="plan",
        entries=entries,
        field_meta={"klorb": {
            "openCount": open_count,
            "closedCount": closed_count,
            "blockedCount": blocked_count,
            "currentTaskId": cur_task_id,
        }},
    )
