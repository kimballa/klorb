# © Copyright 2026 Aaron Kimball
"""A Tool that creates a new todo item tracked in chainlink for this session."""

import logging
from typing import Any

from klorb.tools.tasks.common import ChainlinkClient
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)


class TodoCreateTool(Tool):
    """Creates a new todo item under this session's label and returns its full detail
    (`chainlink issue show` on the new id).

    `blocked_by` records that the new issue is blocked by each given (presumably still
    incomplete) id. `blocks_current_issue` records that the *current* tracked task
    (`Session.cur_chainlink_task_id`, last set by `TodoNext`) is blocked by this new issue --
    raises `ValueError` if there is no current tracked task to block. `blocks_issues` records
    that each given id is blocked by this new issue. See docs/specs/chainlink-task-tracking.md.
    """

    def name(self) -> str:
        return "TodoCreate"

    def category(self) -> str:
        return "TASKS"

    def is_read_only(self) -> bool:
        return False

    def description(self) -> str:
        return (
            "Creates a new todo item for this session and returns its full detail. Use "
            "blocked_by/blocks_current_issue/blocks_issues to record dependencies up front."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Title for the new task."},
                "description": {"type": "string", "description": "Optional longer description."},
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "description": "Default 'medium'.",
                },
                "blocked_by": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Ids of incomplete tasks that block this new one.",
                },
                "blocks_current_issue": {
                    "type": "boolean",
                    "description": (
                        "If true, your current tracked task (from TodoNext) is blocked by this "
                        "new one."
                    ),
                },
                "blocks_issues": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Ids of tasks that depend on (are blocked by) this new one.",
                },
            },
            "required": ["title"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        try:
            title = args["title"]
        except KeyError:
            raise ValueError("Missing required argument: 'title'. Provide the new task's title.")
        description = args.get("description")
        priority = args.get("priority", "medium")
        blocked_by: list[int] = args.get("blocked_by") or []
        blocks_current_issue = bool(args.get("blocks_current_issue", False))
        blocks_issues: list[int] = args.get("blocks_issues") or []

        session = self.context.session
        client = ChainlinkClient(self.context)
        new_id = client.create_issue(title, description=description, priority=priority)
        logger.debug("TodoCreate created issue #%d %r", new_id, title)

        for blocker_id in blocked_by:
            client.block(new_id, blocker_id)
        if blocks_current_issue:
            if session is None or session.cur_chainlink_task_id is None:
                raise ValueError(
                    "blocks_current_issue=true but there is no current tracked task; call "
                    "TodoNext first.")
            client.block(session.cur_chainlink_task_id, new_id)
        for dependent_id in blocks_issues:
            client.block(dependent_id, new_id)

        return client.show_issue(new_id)

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        title = args.get("title", "?")
        if error is not None:
            return f"Create todo: {title!r} failed: {error}"
        if not isinstance(result, dict):
            return f"Create todo: {title!r}"
        return f"Create todo: #{result.get('id')} {result.get('title', title)}"
