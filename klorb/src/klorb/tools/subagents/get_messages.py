# © Copyright 2026 Aaron Kimball
"""Reads and clears every message another agent has sent this session via SendMessage."""

from typing import Any, cast

from klorb.agents.messaging import get_agent_message_queue
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.subagents.common import MESSAGING_TOOL_CATEGORY
from klorb.tools.tool import Tool


class GetMessagesTool(Tool):
    """Pops every undelivered message addressed to this session from the agent-message queue."""

    def name(self) -> str:
        return "GetMessages"

    def category(self) -> str:
        return MESSAGING_TOOL_CATEGORY

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Read every message another agent has sent you via SendMessage since you last "
            "checked. Clears them -- a second call returns only messages that arrived since."
        )

    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}

    def apply(self, args: dict[str, Any]) -> Any:
        context: ToolSetupContext = self.context
        assert context.session is not None
        session = context.session
        messages = get_agent_message_queue(session).pop_all_for(session.id)
        parent = session.parent
        if parent is not None and any(m.sender_id == parent.id for m in messages):
            parent.subagent_tracker.mark_parent_interested(session.id)
        return {
            "messages": [
                {"sender_id": m.sender_id, "sender_role": m.sender_role, "body": m.body}
                for m in messages
            ],
            "parent_id": parent.id if parent is not None else None,
        }

    def format_response(self, apply_output: Any) -> str:
        data: dict[str, Any] = cast(dict, apply_output)
        messages: list[dict[str, str]] = data["messages"]
        parent_id: str | None = data["parent_id"]
        if not messages:
            return "No new messages waiting."

        lines = [f"You have {len(messages)} unread messages:", ""]
        has_parent_message = False
        has_other_message = False
        for i, message in enumerate(messages, start=1):
            is_parent = message["sender_id"] == parent_id
            has_parent_message = has_parent_message or is_parent
            has_other_message = has_other_message or not is_parent
            tag = " (parent)" if is_parent else ""
            lines.append(f"{i}. From {message['sender_id']}{tag}:\n{message['body']}")
            lines.append("")

        if has_parent_message:
            lines.append(
                f"The output of your turn will be sent back to your parent, {parent_id}.")
        if has_other_message:
            lines.append(
                "If you want to respond to any of these non-parent agents, use SendMessage.")
        return "\n".join(lines).rstrip()

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        if error is not None:
            return f"Get messages failed: {error}"
        count = len(result.get("messages", [])) if isinstance(result, dict) else 0
        return f"Get messages ({count} unread)" if count else "Get messages (none unread)"
