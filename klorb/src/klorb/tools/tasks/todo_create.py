# © Copyright 2026 Aaron Kimball
"""A Tool that creates a new todo item tracked in chainlink for this session."""

import logging
from typing import Any

from klorb.tools.tasks._util import maybe_activate_task
from klorb.tools.tasks.common import ChainlinkClient, ChainlinkError, validate_priority
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

    Every argument is validated before the issue is ever created (invalid `priority`, or
    `blocks_current_issue=true` with no current task). Recording the dependencies afterward is
    best-effort atomic: if any `block()` call fails partway through, the new issue is closed
    with a comment explaining why, rather than left behind half-configured, and the original
    error is re-raised.

    `activate` (see `maybe_activate_task`) may pick up the new issue as the session's current
    tracked task — the same thing a `TodoNext` call would do — and adds an `active_task_note`
    field to the returned detail explaining that this happened.
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
            "blocked_by/blocks_current_issue/blocks_issues to record dependencies up front. "
            "The new item may be auto-activated as your current tracked task (as if by "
            "TodoNext) if you don't already have one and it's ready; pass activate=true/false "
            "to force or suppress this."
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
                "activate": {
                    "type": "boolean",
                    "description": (
                        "Force (true) or suppress (false) picking up the new item as your "
                        "current tracked task. Omit for auto mode: activates it if (and only "
                        "if) you don't already have a current task."
                    ),
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
        validate_priority(priority)
        blocked_by: list[int] = args.get("blocked_by") or []
        blocks_current_issue = bool(args.get("blocks_current_issue", False))
        blocks_issues: list[int] = args.get("blocks_issues") or []

        session = self.context.session
        current_id = session.cur_chainlink_task_id if session is not None else None
        if blocks_current_issue and current_id is None:
            raise ValueError(
                "blocks_current_issue=true but there is no current tracked task; call "
                "TodoNext first.")

        client = ChainlinkClient(self.context)
        assert session is not None  # ChainlinkClient() above already requires one
        new_id = client.create_issue(title, description=description, priority=priority)
        logger.debug("TodoCreate created issue #%d %r", new_id, title)

        try:
            for blocker_id in blocked_by:
                client.block(new_id, blocker_id)
            if blocks_current_issue:
                assert current_id is not None  # validated above
                client.block(current_id, new_id)
            for dependent_id in blocks_issues:
                client.block(dependent_id, new_id)
        except Exception as exc:
            logger.warning(
                "TodoCreate: recording dependencies for #%d failed; closing it in error.",
                new_id, exc_info=True)
            try:
                client.comment(new_id, f"Created in error, closing: {exc}")
                client.close_issue(new_id)
            except ChainlinkError:
                logger.warning(
                    "TodoCreate: also failed to close #%d after the earlier failure.",
                    new_id, exc_info=True)
            raise

        result = client.show_issue(new_id)
        note = maybe_activate_task(session, self.context, client, result, activate=args.get("activate"))
        if note is not None:
            result["active_task_note"] = note
        return result

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        title = args.get("title", "?")
        if error is not None:
            return f"Create todo: {title!r} failed: {error}"
        if not isinstance(result, dict):
            return f"Create todo: {title!r}"
        return f"Create todo: #{result.get('id')} {result.get('title', title)}"
