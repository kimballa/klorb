# © Copyright 2026 Aaron Kimball
"""A Tool that lists the todo items chainlink is tracking for this session."""

import logging
from collections.abc import Sequence
from typing import Any

from klorb.agents.registry import get_agent_capabilities
from klorb.tools.exceptions import ToolCallError
from klorb.tools.tasks.common import ChainlinkClient, agent_label
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)

_SUMMARY_DESCRIPTION_MAX_CHARS = 100


class TodoListTool(Tool):
    """Lists the todo items chainlink is tracking under this session's group label (see
    docs/specs/chainlink-task-tracking.md), sorted with the most actionable first: open before
    closed, fewest open blockers before more, higher priority before lower, then oldest id
    before newest.

    `scope` (default `"self"`) additionally filters the full-list path to just the issues
    labeled for this agent (`klorb.tools.tasks.common.agent_label`); `scope="group"` instead
    returns every issue in the group regardless of which agent it's labeled for, but raises
    `ToolCallError` (category `"validation"`) unless this session's role has the
    `see_group_tasks` capability (`klorb.agents.registry.get_agent_capabilities`).

    Aliased as `TaskList`.

    `ids`, if given, narrows the result to just those issues -- `chainlink issue show` directly
    when exactly one id is given (not itself subject to `scope`, since a caller already knows
    the specific id it wants), otherwise the full fetch-enrich-sort pipeline
    (`ChainlinkClient.fetch_and_sort_issues`), `scope`-filtered, then filtered down to the
    requested ids. `include_closed` additionally returns closed issues (still sorted after every
    open one).
    """

    def aliases(self) -> Sequence[str] | None:
        return ["TaskList"]

    def name(self) -> str:
        return "TodoList"

    def category(self) -> str:
        return "TASKS"

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Lists the todo items tracked for this session, sorted with the most actionable "
            "first: open before closed, unblocked before blocked, higher priority before "
            "lower, older before newer. Pass ids to look up specific items instead of every one."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        "Limit to these issue ids. A single id returns that issue's "
                        "full detail with comments. Omit to list all."
                    ),
                },
                "include_closed": {
                    "type": "boolean",
                    "description": "Also include closed issues. Default false.",
                },
                "scope": {
                    "type": "string",
                    "enum": ["self", "group"],
                    "description": (
                        "\"self\" for just your issues, \"group\" for all in project."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        ids: list[int] = args.get("ids") or []
        include_closed = bool(args.get("include_closed", False))
        scope = args.get("scope", "self")
        session = self.context.session
        if scope == "group":
            role_name = session.config.role_name if session is not None else None
            if role_name is None or not get_agent_capabilities(role_name).see_group_tasks:
                raise ToolCallError(
                    f"The {role_name!r} role may not view the whole group's tasks. You must "
                    "set \"scope\": \"self\".", category="validation")
        client = ChainlinkClient(self.context)

        if len(ids) == 1:
            issues = [client.show_issue(ids[0])]
        else:
            issues = client.fetch_and_sort_issues(include_closed=include_closed)
            if scope == "self":
                assert session is not None  # ChainlinkClient() above already requires one
                own_label = agent_label(session.id)
                issues = [issue for issue in issues if own_label in issue.get("labels", [])]
            if ids:
                wanted = set(ids)
                issues = list(filter(lambda issue: issue["id"] in wanted, issues))

        logger.debug("TodoList returning %d issue(s) (scope=%s)", len(issues), scope)
        return {"issues": issues}

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        if error is not None:
            return f"List todos failed: {error}"
        if not isinstance(result, dict):
            return "List todos"
        issues = result.get("issues", [])
        if len(issues) == 1:
            return f"List todos: {self._single_issue_label(issues[0])}"
        return f"List todos ({len(issues)})"

    def _single_issue_label(self, issue: dict[str, Any]) -> str:
        label = f"#{issue.get('id')} {issue.get('title', '')}"
        description = issue.get("description")
        if not description:
            return label
        if len(description) > _SUMMARY_DESCRIPTION_MAX_CHARS:
            description = description[:_SUMMARY_DESCRIPTION_MAX_CHARS] + "..."
        return f"{label} — {description}"
