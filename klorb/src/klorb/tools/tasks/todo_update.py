# © Copyright 2026 Aaron Kimball
"""A Tool that updates a todo item tracked in chainlink for this session."""

import logging
from typing import Any

from klorb.tools.tasks._util import maybe_activate_task
from klorb.tools.tasks.common import ChainlinkClient, validate_priority
from klorb.tools.tasks.todo_next import TodoNextTool
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


def _next_task_note(next_result: dict[str, Any]) -> str:
    """Render `next_result` (`TodoNextTool.apply()`'s own return value) as the `active_task_note`
    TodoUpdate folds into its own result after closing an issue -- see `TodoUpdateTool.apply()`."""
    task = next_result.get("task")
    if task is not None:
        return f'Task #{task["id"]} ("{task.get("title", "")}") is now your current tracked task.'
    if next_result.get("project_complete"):
        return "Every task is done -- there's nothing left to track."
    return "No task is ready right now (everything remaining is blocked); you have no current tracked task."


class TodoUpdateTool(Tool):
    """Updates one todo item and returns its full detail (`chainlink issue show` on `id`) —
    every other argument is optional and independently applied: title/description/priority
    changes, adding/dropping dependencies, adding a comment, and closing/reopening, in that
    order (close/reopen last, so a comment added in the same call lands before the issue closes).

    Closing the issue (`close=True`) also, unless `activate=False`, immediately picks up
    whatever's next as the session's current tracked task -- exactly as if the model had called
    `TodoNext` itself right afterward (it's `TodoNextTool` doing the picking, invoked directly)
    -- and adds `next_task_id`/`next_task_title` (either may be `None`, if nothing is ready or
    every task is done) plus an `active_task_note` summarizing the outcome to the returned detail.

    When the call doesn't close the issue, `activate` (see `maybe_activate_task`) may instead
    pick up the *updated* issue itself as the session's current tracked task, adding the same
    `active_task_note` field (but no `next_task_id`/`next_task_title`, since there's no separate
    "next" task in that case -- it's the issue just updated). See
    docs/specs/chainlink-task-tracking.md.
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
            "id is optional; omitted are unchanged. Closing also picks up whatever "
            "is next as your current tracked task (as if you called TodoNext), reported back "
            "as next_task_id/next_task_title. Otherwise, this item itself may be auto-activated "
            "as your current tracked task if you don't already have one and it's ready. Pass "
            "activate=true/false to force or suppress either behavior."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Id of the task to update."},
                "close": {"type": "boolean", "description": "Close issue if true."},
                "reopen": {"type": "boolean", "description": "Reopen issue if true."},
                "depends_on": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Ids to add as blockers of task.",
                },
                "drop_dependency": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Ids to remove as blockers of task.",
                },
                "add_comment": {"type": "string", "description": "Comment to add."},
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
                        "If closing: false suppresses picking up whatever's next as your "
                        "current tracked task (omit or true to pick it up). Otherwise: force "
                        "(true) or suppress (false) picking up this item itself as your current "
                        "tracked task; omit for auto mode, which activates only if you have no "
                        "current task."
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
                # it keeps reporting as the current task (to the plan-update snapshot, and to the
                # standing interjection) until the auto-advance below (or a later TodoNext call,
                # if activate=False suppressed it) picks something else.
                session.set_chainlink_task(None)
        if args.get("reopen"):
            client.reopen_issue(issue_id)

        fields = _changed_fields(args)
        logger.debug(
            "TodoUpdate applied to issue #%d: %s", issue_id, ", ".join(fields) or "no changes")
        result = client.show_issue(issue_id)
        activate = args.get("activate")
        if closing:
            if activate is not False:
                next_result = TodoNextTool(self.context).apply({})
                next_task = next_result.get("task")
                result["next_task_id"] = next_task["id"] if next_task is not None else None
                result["next_task_title"] = next_task.get("title") if next_task is not None else None
                result["active_task_note"] = _next_task_note(next_result)
        else:
            note = maybe_activate_task(session, self.context, client, result, activate=activate)
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
