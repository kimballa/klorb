# © Copyright 2026 Aaron Kimball
"""Reads unread messages from the broadcast chat room every agent and the user share."""

from typing import Any, cast

from pydantic import BaseModel, Field

from klorb.agents.chat import get_chat_channel
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.subagents.common import MESSAGING_TOOL_CATEGORY
from klorb.tools.tool import Tool


class ReadChatParameters(BaseModel):
    limit: int | None = Field(
        default=None,
        description=(
            "Cap on how many unread messages to return in this call, hard-bounded by "
            "tools.chat.maxReadPerCall regardless."))


class ReadChatTool(Tool):
    """Returns this session's unread chat-room messages, oldest first, and advances its own
    high-water mark to the last one returned."""

    def name(self) -> str:
        return "ReadChat"

    def category(self) -> str:
        return MESSAGING_TOOL_CATEGORY

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Read the chat room's unread messages since you last checked, oldest first. "
            "Advances your own read position -- a later call returns only messages posted "
            "since."
        )

    def parameters(self) -> type[BaseModel]:
        return ReadChatParameters

    def apply(self, args: dict[str, Any]) -> Any:
        context: ToolSetupContext = self.context
        assert context.session is not None
        session = context.session
        requested_limit = args.get("limit")
        max_per_call = context.process_config.chat_max_read_per_call
        limit = max_per_call if requested_limit is None else min(requested_limit, max_per_call)
        channel = get_chat_channel(session)
        messages = channel.read_and_advance(session.id, limit=limit)
        return {
            "messages": [
                {
                    "seq": message.seq, "sender_id": message.sender_id,
                    "timestamp": message.timestamp.isoformat(), "body": message.body,
                    "mentions": message.mentions,
                }
                for message in messages
            ],
            "count": len(messages),
            "remaining_unread": channel.unread_count(session.id),
        }

    def format_response(self, apply_output: Any) -> str:
        data: dict[str, Any] = cast(dict, apply_output)
        messages: list[dict[str, Any]] = data["messages"]
        if not messages:
            return "No new messages waiting."

        lines = [f"You have {len(messages)} unread chat message(s):", ""]
        for message in messages:
            lines.append(f"#{message['seq']} from {message['sender_id']}:\n{message['body']}")
            lines.append("")
        if data["remaining_unread"]:
            lines.append(
                f"{data['remaining_unread']} more unread message(s) remain -- call ReadChat "
                "again to see them.")
        return "\n".join(lines).rstrip()

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        if error is not None:
            return f"Read chat failed: {error}"
        count = len(result.get("messages", [])) if isinstance(result, dict) else 0
        return f"Read chat ({count} unread)" if count else "Read chat (none unread)"
