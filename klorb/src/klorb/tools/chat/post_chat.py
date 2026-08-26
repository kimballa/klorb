# © Copyright 2026 Aaron Kimball
"""Posts a message to the broadcast chat room every agent and the user share."""

from typing import Any, cast

from pydantic import BaseModel, Field

from klorb.agents.policy import notify_chat_mention
from klorb.tools.exceptions import ToolCallError
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.subagents.common import MESSAGING_TOOL_CATEGORY
from klorb.tools.tool import Tool


class PostChatParameters(BaseModel):
    message: str = Field(description=(
        "The message to post to the chat room. @mention another agent by its session id to "
        "notify it."))


class PostChatTool(Tool):
    """Posts `message` to the session tree's shared chat room, resolving any `@mention`s
    against the live session tree."""

    def name(self) -> str:
        return "PostChat"

    def category(self) -> str:
        return MESSAGING_TOOL_CATEGORY

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Post a message to the shared chat room all agents and the user can read "
            "asynchronously. @mention other agents to explicitly nudge them to check chat "
            "sooner."
        )

    def parameters(self) -> type[BaseModel]:
        return PostChatParameters

    def apply(self, args: dict[str, Any]) -> Any:
        context: ToolSetupContext = self.context
        assert context.session is not None
        session = context.session
        message = args["message"]
        if not message:
            raise ToolCallError("message must not be empty.", category="validation")
        channel = session.chat_channel
        chat_message = channel.post(session.id, message, session)
        for mentioned_id in chat_message.mentions:
            notify_chat_mention(context.process_config, channel, session.id, session, mentioned_id)
        return {
            "seq": chat_message.seq,
            "mentions": chat_message.mentions,
            "unresolved_mentions": chat_message.unresolved_mentions,
            "note": (
                "Other agents receive this asynchronously via their own ReadChat call, not "
                "immediately."
            ),
        }

    def format_response(self, apply_output: Any) -> str:
        data: dict[str, Any] = cast(dict, apply_output)
        parts = [f"Posted to chat room (#{data['seq']})."]
        if data["mentions"]:
            parts.append(f"Mentioned: {', '.join(data['mentions'])}.")
        if data["unresolved_mentions"]:
            parts.append(
                f"Did not resolve to a known participant: "
                f"{', '.join('@' + token for token in data['unresolved_mentions'])}.")
        parts.append(data["note"])
        return " ".join(parts)

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        if error is not None:
            return f"Post to chat room failed: {error}"
        data: dict[str, Any] = result if isinstance(result, dict) else {}
        mention_count = len(data.get("mentions", []))
        suffix = f" ({mention_count} mentioned)" if mention_count else ""
        return f"Posted to chat room (#{data.get('seq', '?')}){suffix}"
