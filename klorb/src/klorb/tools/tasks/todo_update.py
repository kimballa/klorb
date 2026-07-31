# © Copyright 2026 Aaron Kimball
"""A Tool that updates a todo item tracked in chainlink for this session."""

import logging
from typing import Any

from klorb.tools.tasks._util import maybe_activate_task
from klorb.tools.tasks.common import ChainlinkClient, validate_priority
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)


def _changed_fields(args: dict[str, Any]) -> list[str]:
    """Which of `args`'s optional fields are actually set, in application order -- used both for
    `apply()`'s debug log and `summary()`'s rendering, so the two never drift apart."""
    fields = []
    if args.get("new_title") is not None:
        fields.append("title")
    if args.get("new_description") is not None:
        fields.append("description")
    if args.get("new_priority") is not None:
        fields.append("priority")
    if args.get("depends_on"):
        fields.append("depends_on")
    if args.get("drop_dependency"):
        fields.append("drop_dependency")
    if args.get("add_comment"):
        fields.append("comment")
    if args.get("close"):
        fields.append("closed")
    if args.get("reopen"):
        fields.append("reopened")
    return fields


class TodoUpdateTool(Tool):
    """Updates one todo item and returns its full detail (`chainlink issue show` on `id`) —
    every other argument is optional and independently applied: title/description/priority
    changes, adding/dropping dependencies, adding a comment, and closing/reopening, in that
    order (close/reopen last, so a comment added in the same call lands before the issue closes).
    Closing the issue (`close=True`) adds a `required_next_tool_call` field to the returned
    detail, telling the model it must call `TodoNext` next.

    Unless the update closes the issue, `activate` (see `maybe_activate_task`) may also pick up
    the updated issue as the session's current tracked task — the same thing a `TodoNext` call
    would do — and add an `active_task_note` field to the returned detail explaining that this
    happened. See docs/specs/chainlink-task-tracking.md.
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
            "id is optional; omitted ones are left unchanged. Unless you're closing the item, "
            "it may be auto-activated as your current tracked task (as if by TodoNext) if you "
            "don't already have one and the item is ready; pass activate=true/false to force or "
            "suppress this."
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
                "activate": {
                    "type": "boolean",
                    "description": (
                        "Force (true) or suppress (false) picking up this item as your current "
                        "tracked task. Omit for auto mode: activates it if (and only if) you "
                        "don't already have a current task."
                    ),
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
        new_priority = args.get("new_priority")
        if new_priority is not None:
            validate_priority(new_priority)
        client = ChainlinkClient(self.context)
        session = self.context.session
        assert session is not None  # ChainlinkClient() above already requires one

        client.update_issue(
            issue_id, title=args.get("new_title"), description=args.get("new_description"),
            priority=new_priority)
        for blocker_id in args.get("depends_on") or []:
            client.block(issue_id, blocker_id)
        for blocker_id in args.get("drop_dependency") or []:
            client.unblock(issue_id, blocker_id)
        if args.get("add_comment"):
            client.comment(issue_id, args["add_comment"])
        closing = bool(args.get("close"))
        if closing:
            client.close_issue(issue_id)
            if session.cur_chainlink_task_id == issue_id:
                # Closing the session's own tracked task must clear it immediately -- otherwise
                # it keeps reporting as the current task (to the plan-update snapshot, and to
                # TodoNext's standing interjection) until the model happens to call TodoNext
                # again, which may never happen once the last task is done.
                session.set_chainlink_task(None)
        if args.get("reopen"):
            client.reopen_issue(issue_id)

        fields = _changed_fields(args)
        logger.debug(
            "TodoUpdate applied to issue #%d: %s", issue_id, ", ".join(fields) or "no changes")
        result = client.show_issue(issue_id)
        if closing:
            result["required_next_tool_call"] = (
                "Call TodoNext right now, before any other tool call, to pick up whatever's "
                "next -- don't leave this session without a tracked task."
            )
        else:
            note = maybe_activate_task(
                session, self.context, client, result, activate=args.get("activate"))
            if note is not None:
                result["active_task_note"] = note
        return result

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        issue_id = args.get("id", "?")
        fields = ", ".join(_changed_fields(args))
        suffix = f" ({fields})" if fields else ""
        if error is not None:
            return f"Update todo #{issue_id} failed: {error}"
        if not isinstance(result, dict):
            return f"Update todo #{issue_id}{suffix}"
        return f"Update todo #{result.get('id', issue_id)} {result.get('title', '')}{suffix}"
