# © Copyright 2026 Aaron Kimball
"""A Tool that lists the todo items chainlink is tracking for this session."""

import logging
from typing import Any

from klorb.tools.tasks.common import ChainlinkClient, fetch_and_sort_issues
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)

_SUMMARY_DESCRIPTION_MAX_CHARS = 100


class TodoListTool(Tool):
    """Lists the todo items chainlink is tracking under this session's label (see
    docs/specs/chainlink-task-tracking.md), sorted with the most actionable first: open before
    closed, fewest open blockers before more, higher priority before lower, then oldest id
    before newest.

    `ids`, if given, narrows the result to just those issues -- `chainlink issue show` directly
    when exactly one id is given, otherwise the full fetch-enrich-sort pipeline
    (`fetch_and_sort_issues`) filtered down to the requested ids afterward. `include_closed`
    additionally returns closed issues (still sorted after every open one).
    """

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
                        "Limit the result to these issue ids. A single id returns that issue's "
                        "full detail, comments included. Omit to list every issue."
                    ),
                },
                "include_closed": {
                    "type": "boolean",
                    "description": "Also include closed issues. Default false.",
                },
            },
            "required": [],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        ids: list[int] = args.get("ids") or []
        include_closed = bool(args.get("include_closed", False))
        client = ChainlinkClient(self.context)

        if len(ids) == 1:
            issues = [client.show_issue(ids[0])]
        else:
            issues = fetch_and_sort_issues(client, include_closed=include_closed)
            if ids:
                wanted = set(ids)
                issues = list(filter(lambda issue: issue["id"] in wanted, issues))

        logger.debug("TodoList returning %d issue(s)", len(issues))
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
