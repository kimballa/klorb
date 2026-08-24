# © Copyright 2026 Aaron Kimball
"""Sends a message to any agent in the session tree, whether it's idle or mid-turn."""

from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from klorb.agents.messaging import get_agent_message_queue
from klorb.agents.policy import try_wake_next_queued_agent
from klorb.agents.registry import get_agent_capabilities
from klorb.agents.runtime import find_session_in_group
from klorb.session import Session
from klorb.tools.exceptions import ToolCallError
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.subagents.common import MESSAGING_TOOL_CATEGORY
from klorb.tools.tool import Tool


class SendMessageParameters(BaseModel):
    id: str = Field(description=(
        "The target agent's session id, from the AgentGroup table -- any agent in the group, "
        "not just your own subagents."))
    message: str = Field(description="The message to send.")


def _target_state(target: Session) -> Literal["root", "running", "dormant"]:
    """`target`'s state as it matters to `SendMessage`, right before it's actually enqueued:
    `"root"` for a top-level session (no parent, never actively dispatched), `"running"`/
    `"dormant"` for a subagent, read from its own tracked `SubagentHandle`."""
    if target.parent is None:
        return "root"
    handle = target.parent.subagent_tracker.current_handle(target.id)
    return "running" if handle is not None and handle.state == "running" else "dormant"


class SendMessageTool(Tool):
    """Delivers a message to a dormant agent immediately by starting its next turn, or -- if the
    target is busy or at a concurrency limit -- queues it for delivery via `GetMessages`."""

    def name(self) -> str:
        return "SendMessage"

    def category(self) -> str:
        return MESSAGING_TOOL_CATEGORY

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Send a message to any agent in the group, whether it's idle or still running. If "
            "it's idle, this starts its next turn right away. If it's busy (or the most that may "
            "run at once are already running), the message is queued and it will see it via "
            "GetMessages once it's free."
        )

    def parameters(self) -> type[BaseModel]:
        return SendMessageParameters

    def apply(self, args: dict[str, Any]) -> Any:
        context: ToolSetupContext = self.context
        assert context.session is not None
        sender = context.session
        if not get_agent_capabilities(sender.config.role_name).send_messages:
            raise ToolCallError(
                f"The {sender.config.role_name!r} role may not send messages to other agents.",
                category="validation")
        target_id = args["id"]
        if target_id == sender.id:
            raise ToolCallError("You cannot send a message to yourself.", category="validation")
        target = find_session_in_group(sender, target_id)
        if target is None:
            raise ToolCallError(f"No such agent: {target_id!r}", category="validation")

        pre_state = _target_state(target)
        get_agent_message_queue(sender).enqueue(
            sender.id, sender.config.role_name, target.id, args["message"])
        target.notify_new_message()
        try_wake_next_queued_agent(context.process_config, sender)

        if pre_state in ("root", "running"):
            status = "busy"
        else:
            handle = target.parent.subagent_tracker.current_handle(target.id) if target.parent else None
            status = "delivered" if handle is not None and handle.state == "running" else "capacity"
        return {"status": status, "target_id": target.id}

    def format_response(self, apply_output: Any) -> str:
        data: dict[str, Any] = cast(dict, apply_output)
        target_id = data["target_id"]
        status = data["status"]
        if status == "delivered":
            return f"Message delivered to agent {target_id}; its next turn is now running."
        if status == "capacity":
            return (
                f"Agent {target_id} could not be started immediately (concurrency limit) -- your "
                "message is queued and it will receive it once capacity is available."
            )
        return (
            f"Agent {target_id} is busy -- your message is queued and it will see it via "
            "GetMessages."
        )

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        base = f"Send message to agent {args.get('id', '?')}"
        return base if error is None else f"{base} failed: {error}"
