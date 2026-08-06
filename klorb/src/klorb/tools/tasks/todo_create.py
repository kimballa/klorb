# © Copyright 2026 Aaron Kimball
"""A Tool that creates a new todo item tracked in chainlink for this session."""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from klorb.agents.registry import get_agent_capabilities
from klorb.agents.runtime import find_session_in_group
from klorb.tools.exceptions import ToolCallError
from klorb.tools.tasks._util import maybe_activate_task
from klorb.tools.tasks.common import (
    ALL_LABEL,
    ChainlinkClient,
    ChainlinkError,
    agent_label,
    validate_priority,
)
from klorb.tools.tool import Tool

if TYPE_CHECKING:
    from klorb.session import Session

logger = logging.getLogger(__name__)


def _resolve_assignment_label(session: "Session", assign_to_raw: Any) -> str:
    """Validate `assign_to` and return the chainlink label the new issue should carry:
    `agent_label(id)` for a specific agent (this session itself, or another one in its group),
    or `ALL_LABEL` for a group-wide, unclaimed task. Raises `ToolCallError` (category
    `"validation"`) if the requested assignment isn't permitted -- see
    docs/specs/chainlink-task-tracking.md's "Task assignment" section.

    `assign_to_raw` of `None`/`""`/`"self"` targets this session itself, requiring its own role
    to have `accepts_tasks`. `"all"` (`ALL_LABEL`) always succeeds. Anything else names another
    agent's session id, requiring this session's role to have `assigns_tasks`, that id to
    resolve to a live session in the same group (`find_session_in_group`), and that agent's own
    role to have `accepts_tasks`.
    """
    role_name = session.config.role_name
    if assign_to_raw in (None, "", "self"):
        if not get_agent_capabilities(role_name).accepts_tasks:
            raise ToolCallError(
                f"The {role_name!r} role may not be assigned tasks; pass assign_to=\"all\" or "
                "another agent's id instead.", category="validation")
        return agent_label(session.id)
    if assign_to_raw == ALL_LABEL:
        return ALL_LABEL
    if not get_agent_capabilities(role_name).assigns_tasks:
        raise ToolCallError(
            f"The {role_name!r} role may not assign tasks to other agents.", category="validation")
    target = find_session_in_group(session, assign_to_raw)
    if target is None:
        raise ToolCallError(f"No such agent in this group: {assign_to_raw!r}", category="validation")
    if not get_agent_capabilities(target.config.role_name).accepts_tasks:
        raise ToolCallError(
            f"Agent {assign_to_raw!r} (role {target.config.role_name!r}) may not be assigned "
            "tasks.", category="validation")
    return agent_label(assign_to_raw)


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

    `assign_to` (see `_resolve_assignment_label`) picks which agent's chainlink label the new
    issue carries — this session itself (the default), `"all"` for a group-wide unclaimed task,
    or another agent's session id — raising `ToolCallError` if the requested assignment isn't
    permitted for either agent's role capabilities. See docs/specs/chainlink-task-tracking.md's
    "Task assignment" section.

    Aliased as `TodoWrite`.
    """

    def aliases(self) -> Sequence[str] | None:
        return ["TodoWrite"]

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
                "assign_to": {
                    "type": "string",
                    "description": (
                        "Which agent should own the new task: omit, \"\", or \"self\" for "
                        "yourself; \"all\" to leave it unclaimed for any agent in your group to "
                        "pick up; or another agent's session id to assign it to them directly "
                        "(requires the ability to assign tasks to others)."
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
        assignment_label = _resolve_assignment_label(session, args.get("assign_to"))
        new_id = client.create_issue(title, description=description, priority=priority)
        logger.debug("TodoCreate created issue #%d %r", new_id, title)

        try:
            client.add_label(new_id, assignment_label)
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
