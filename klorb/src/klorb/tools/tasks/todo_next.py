# © Copyright 2026 Aaron Kimball
"""A Tool that picks the next ready todo item to work on and tracks it as the session's
current task."""

import logging
from typing import Any

from klorb.tools.tasks._util import set_current_task
from klorb.tools.tasks.common import (
    ChainlinkClient,
    ChainlinkError,
    fetch_and_sort_issues,
    open_blocker_count,
)
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)


class TodoNextTool(Tool):
    """Picks the next ready (zero open blockers) todo item under this session's label, in
    `fetch_and_sort_issues`'s sort order, and sets it as `Session.cur_chainlink_task_id`.

    If a current task is already tracked and still open, that same task is returned again
    (nothing new is picked) along with a `message` telling the model to finish and close it
    first -- `TodoNext` never silently abandons a task the model hasn't closed yet.

    Returns `work_exists=False, project_complete=True, task=None` when there are no open issues
    at all under this label. Returns `work_exists=True, project_complete=False, task=None` when
    open issues remain but none are ready yet (every one is still blocked) -- there's nothing
    actionable to hand back right now, but the project isn't done either. Otherwise returns the
    top-sorted ready issue and registers a standing `<SystemInterjection>` (see
    `klorb.tools.tasks._util.standing_interjection_provider`) reminding the model of it on every
    subsequent turn, not just this one.

    Only ever reads from chainlink (`issue list`/`issue show`) -- picking a task (or re-returning
    the current one) mutates `Session.cur_chainlink_task_id` and the standing interjection
    registry, both in-memory session state that's never written to chainlink itself, so
    `is_read_only()` is `True`.
    """

    def name(self) -> str:
        return "TodoNext"

    def category(self) -> str:
        return "TASKS"

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Returns the next ready todo item to work on for this session (or reports that "
            "none is ready, or that every item is done) and sets it as your current tracked "
            "task. If you already have a current, still-open task, returns that one again "
            "instead of picking a new one -- close it first."
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

        current_id = session.cur_chainlink_task_id
        if current_id is not None:
            current_task = self._still_open_current_task(client, current_id)
            if current_task is not None:
                logger.debug("TodoNext: current task #%d is still open; returning it again.", current_id)
                set_current_task(session, self.context, current_id)
                return {
                    "work_exists": True,
                    "project_complete": False,
                    "task": current_task,
                    "message": (
                        f"You already have a current task, #{current_id}. Finish it and close "
                        "it with TodoUpdate before calling TodoNext again."
                    ),
                }

        issues = fetch_and_sort_issues(client, include_closed=False)

        if not issues:
            set_current_task(session, self.context, None)
            logger.debug("TodoNext: no open issues under label %r", session.get_chainlink_label())
            return {"work_exists": False, "project_complete": True, "task": None}

        open_ids = {issue["id"] for issue in issues}
        ready = [issue for issue in issues if open_blocker_count(issue, open_ids) == 0]
        task = ready[0] if ready else None
        set_current_task(session, self.context, task["id"] if task is not None else None)

        if task is not None:
            logger.debug("TodoNext: next task is #%d", task["id"])
        else:
            logger.debug("TodoNext: %d open issue(s), none ready (all blocked)", len(issues))

        return {"work_exists": True, "project_complete": False, "task": task}

    def _still_open_current_task(self, client: ChainlinkClient, task_id: int) -> dict[str, Any] | None:
        """Return `task_id`'s full detail if it still resolves and is still open, else `None`
        (deleted, or already closed -- either way there's nothing to hand back again)."""
        try:
            issue = client.show_issue(task_id)
        except ChainlinkError:
            return None
        return issue if issue.get("status") == "open" else None

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        if error is not None:
            return f"Next todo failed: {error}"
        if not isinstance(result, dict):
            return "Next todo"
        task = result.get("task")
        if task is None:
            return "Next todo: nothing ready" if result.get("work_exists") else "Next todo: all done"
        return f"Next todo: #{task.get('id')} {task.get('title', '')}"
