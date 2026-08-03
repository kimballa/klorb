# © Copyright 2026 Aaron Kimball
"""`MessageSubagentTool`: sends a follow-up message to an already-finished subagent, resuming
its turn asynchronously on a background thread. See docs/specs/subagents.md's
"MessageSubagent" section.
"""

from typing import Any

from pydantic import BaseModel, Field

from klorb.agents.policy import check_concurrency_limits, dispatch_subagent_turn
from klorb.tools.exceptions import ToolCallError
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.subagents.common import SUBAGENT_TOOL_CATEGORY
from klorb.tools.tool import Tool


class MessageSubagentParameters(BaseModel):
    id: str = Field(description="The subagent id, as returned by CreateSubagent.")
    message: str = Field(description="The follow-up message to send to the subagent.")


class MessageSubagentTool(Tool):
    """Resumes a finished subagent's conversation: looks up its `SubagentHandle` on the calling
    session's own `subagent_tracker`, requires it to be in the `"finished"` state, then runs
    the follow-up turn on a background thread like `CreateSubagentTool` ran the first one."""

    def name(self) -> str:
        return "MessageSubagent"

    def category(self) -> str:
        return SUBAGENT_TOOL_CATEGORY

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Send a follow-up message to a subagent that has already finished its previous "
            "turn. Fails if the subagent is still running -- use WaitForSubagent first."
        )

    def parameters(self) -> type[BaseModel]:
        return MessageSubagentParameters

    def apply(self, args: dict[str, Any]) -> Any:
        context: ToolSetupContext = self.context
        assert context.session is not None
        tracker = context.session.subagent_tracker
        handle = next(
            (h for h in tracker.handles() if h.session.id == args["id"]), None)
        if handle is None:
            raise ToolCallError(f"No such subagent: {args['id']!r}", category="validation")
        if handle.state != "finished":
            raise ToolCallError(
                "That subagent is not done its turn yet -- call WaitForSubagent to wait for "
                "it to finish first.", category="validation")
        # Resuming a dormant (finished) subagent turns it back into a running one, so it costs
        # a concurrency slot exactly like CreateSubagent starting a new one does.
        check_concurrency_limits(context, context.session)
        dispatch_subagent_turn(
            context.session, handle.session, handle.role, handle.title, args["message"])
        return {
            "subagent_id": handle.session.id,
            "note": (
                "The subagent is running again. Use WaitForSubagent to wait "
                "for its reply, or continue with other work until it's delivered automatically."
            ),
        }

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        base = f"Message subagent {args.get('id', '?')}"
        return base if error is None else f"{base} failed: {error}"
