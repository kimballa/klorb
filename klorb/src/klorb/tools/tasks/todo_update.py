# © Copyright 2026 Aaron Kimball
"""A Tool that updates a todo item tracked in chainlink for this session."""

import logging
from typing import Any

from klorb.tools.tasks.common import ChainlinkClient
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)


class TodoUpdateTool(Tool):
    """Updates one todo item and returns its full detail (`chainlink issue show` on `id`) —
    every other argument is optional and independently applied: title/description/priority
    changes, adding/dropping dependencies, adding a comment, and closing/reopening, in that
    order (close/reopen last, so a comment added in the same call lands before the issue closes).
    See docs/specs/chainlink-task-tracking.md.
    """

    def name(self) -> str:
        return "TodoUpdate"

    def category(self) -> str:
        return "TASKS"

    def is_read_only(self) -> bool:
        return False

    def description(self) -> str:
        return (
            "Updates a todo item: title/description/priority, dependencies (depends_on/"
            "drop_dependency), a comment, and/or closing or reopening it. Every field besides "
            "id is optional; omitted ones are left unchanged."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Id of the task to update."},
                "close": {"type": "boolean", "description": "If true, close the issue."},
                "reopen": {"type": "boolean", "description": "If true, reopen a closed issue."},
                "depends_on": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Ids to add as blockers of this task.",
                },
                "drop_dependency": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Ids to remove as blockers of this task.",
                },
                "add_comment": {"type": "string", "description": "Comment text to add."},
                "new_title": {"type": "string", "description": "Replacement title."},
                "new_description": {"type": "string", "description": "Replacement description."},
                "new_priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "description": "Replacement priority.",
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        try:
            issue_id = args["id"]
        except KeyError:
            raise ValueError("Missing required argument: 'id'. Provide the task id to update.")
        client = ChainlinkClient(self.context)

        client.update_issue(
            issue_id,
            title=args.get("new_title"),
            description=args.get("new_description"),
            priority=args.get("new_priority"),
        )
        for blocker_id in args.get("depends_on") or []:
            client.block(issue_id, blocker_id)
        for blocker_id in args.get("drop_dependency") or []:
            client.unblock(issue_id, blocker_id)
        if args.get("add_comment"):
            client.comment(issue_id, args["add_comment"])
        if args.get("close"):
            client.close_issue(issue_id)
        if args.get("reopen"):
            client.reopen_issue(issue_id)

        logger.debug("TodoUpdate applied to issue #%d", issue_id)
        return client.show_issue(issue_id)

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        issue_id = args.get("id", "?")
        if error is not None:
            return f"Update todo #{issue_id} failed: {error}"
        if not isinstance(result, dict):
            return f"Update todo #{issue_id}"
        return f"Update todo #{result.get('id', issue_id)} {result.get('title', '')}"
