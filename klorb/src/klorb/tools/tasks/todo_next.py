# © Copyright 2026 Aaron Kimball
"""A Tool that picks the next ready todo item to work on and tracks it as the session's
current task."""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from klorb.tools.tasks.common import (
    ChainlinkClient,
    ChainlinkError,
    fetch_and_sort_issues,
    open_blocker_count,
)
from klorb.tools.tool import Tool

if TYPE_CHECKING:
    from klorb.session import Session
    from klorb.tools.setup_context import ToolSetupContext

logger = logging.getLogger(__name__)

_STANDING_INTERJECTION_SUBJECT = "ChainlinkCurrentTask"


def _standing_interjection_provider(
    session: "Session", context: "ToolSetupContext",
) -> Callable[[], str | None]:
    """Build the `Session.register_standing_interjection` provider for the current tracked
    task: a message naming its id and title, re-resolved fresh on every poll (`Session.
    cur_chainlink_task_id` only stores the id, not the title), for as long as one is set --
    `None` once `TodoNext` clears it (no ready or open work left)."""

    def provider() -> str | None:
        task_id = session.cur_chainlink_task_id
        if task_id is None:
            return None
        try:
            issue = ChainlinkClient(context).show_issue(task_id)
        except (ChainlinkError, ValueError):
            return None
        title = issue.get("title", "(untitled)")
        return (
            f"Your current tracked task is #{task_id}: {title}. Record a comment on it "
            "(TodoUpdate add_comment) whenever you make meaningful progress, learn something "
            "new, or make a decision; close it (TodoUpdate close) once it's done and verified, "
            "then call TodoNext for the next one."
        )

    return provider


class TodoNextTool(Tool):
    """Picks the next ready (zero open blockers) todo item under this session's label, in
    `fetch_and_sort_issues`'s sort order, and sets it as `Session.cur_chainlink_task_id`.

    Returns `work_exists=False, project_complete=True, task=None` when there are no open issues
    at all under this label. Returns `work_exists=True, project_complete=False, task=None` when
    open issues remain but none are ready yet (every one is still blocked) -- there's nothing
    actionable to hand back right now, but the project isn't done either. Otherwise returns the
    top-sorted ready issue and registers a standing `<SystemInterjection>` (see
    `_standing_interjection_provider`) reminding the model of it on every subsequent turn, not
    just this one.

    Mutates session state as a side effect of what reads like a query, so `is_read_only()` is
    `False` even though the only chainlink calls it makes are reads (`issue list`/`issue show`).
    """

    def name(self) -> str:
        return "TodoNext"

    def category(self) -> str:
        return "TASKS"

    def is_read_only(self) -> bool:
        return False

    def description(self) -> str:
        return (
            "Returns the next ready todo item to work on for this session (or reports that "
            "none is ready, or that every item is done) and sets it as your current tracked "
            "task."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        session = self.context.session
        if session is None:
            raise ValueError("TodoNext requires an active Session.")
        client = ChainlinkClient(self.context)
        issues = fetch_and_sort_issues(client, include_closed=False)

        if not issues:
            session.cur_chainlink_task_id = None
            logger.debug("TodoNext: no open issues under label %r", session.id)
            return {"work_exists": False, "project_complete": True, "task": None}

        open_ids = {issue["id"] for issue in issues}
        ready = [issue for issue in issues if open_blocker_count(issue, open_ids) == 0]
        task = ready[0] if ready else None
        session.cur_chainlink_task_id = task["id"] if task is not None else None

        if task is not None:
            logger.debug("TodoNext: next task is #%d", task["id"])
            session.register_standing_interjection(
                _STANDING_INTERJECTION_SUBJECT, _standing_interjection_provider(session, self.context))
        else:
            logger.debug("TodoNext: %d open issue(s), none ready (all blocked)", len(issues))

        return {"work_exists": True, "project_complete": False, "task": task}

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        if error is not None:
            return f"Next todo failed: {error}"
        if not isinstance(result, dict):
            return "Next todo"
        task = result.get("task")
        if task is None:
            return "Next todo: nothing ready" if result.get("work_exists") else "Next todo: all done"
        return f"Next todo: #{task.get('id')} {task.get('title', '')}"
