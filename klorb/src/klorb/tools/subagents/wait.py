# © Copyright 2026 Aaron Kimball
"""`WaitForSubagentTool`: suspends the calling turn until one of its own subagents finishes and
has something to say.
"""

from typing import Any

from klorb.agents.runtime import SubagentHandle
from klorb.tools.exceptions import ToolCallError
from klorb.tools.interruptible_tool import InterruptibleTool
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.subagents.common import SUBAGENT_TOOL_CATEGORY

_POLL_TIMEOUT_SECONDS = 0.2
"""How often the wait loop re-checks `_active_cancel_event()` between polls of the completion
queue."""


class WaitForSubagentTool(InterruptibleTool):
    """Blocks the calling session's own dispatch thread, polling `context.session.
    subagent_tracker` until at least one completed-but-undelivered subagent arrives or the turn
    is cancelled. Once one arrives, every *other* subagent that also finished in the meantime is
    delivered in the same call (oldest first) rather than forcing a separate `WaitForSubagent`
    round trip per completion. Fails immediately, without suspending, if the caller has no
    outstanding subagents.
    """

    def name(self) -> str:
        return "WaitForSubagent"

    def category(self) -> str:
        return SUBAGENT_TOOL_CATEGORY

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Wait for your own subagents to finish and deliver their output. Suspends until at "
            "least one subagent completes its turn, then returns every subagent that has finished "
            "so far. Fails immediately if you have no subagents currently running or awaiting "
            "delivery."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        del args
        context: ToolSetupContext = self.context
        assert context.session is not None
        tracker = context.session.subagent_tracker
        if not tracker.has_undelivered():
            raise ToolCallError(
                "You have no subagents currently running or awaiting delivery.",
                category="validation")
        cancel_event = self._active_cancel_event()
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return {"incomplete": True, "incomplete_reason": "user_cancel"}
            handles: list[SubagentHandle] = tracker.pop_all_completed(timeout=_POLL_TIMEOUT_SECONDS)
            if handles:
                return {"completed": [_format_completion(handle) for handle in handles]}

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        if error is not None:
            return f"Wait for subagent failed: {error}"
        completed = result.get("completed") if isinstance(result, dict) else None
        if completed:
            first, extra = completed[0], len(completed) - 1
            suffix = f" (+{extra} more)" if extra else ""
            return f"Wait for subagent: {first.get('title', '?')} finished{suffix}"
        return "Wait for subagent"


def _format_completion(handle: SubagentHandle) -> dict[str, Any]:
    assert handle.output is not None
    return {
        "subagent_id": handle.session.id,
        "role": handle.role,
        "title": handle.title,
        "output": handle.output,
    }
