# © Copyright 2026 Aaron Kimball
"""Suspends the calling turn until one of its own subagents finishes and has something to say."""

import time
from collections.abc import Sequence
from typing import Any

from klorb.agents.runtime import SubagentHandle
from klorb.tools.exceptions import ToolCallError, ToolInterruptError
from klorb.tools.interruptible_tool import UserInterruptibleTool
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.subagents.common import SUBAGENT_TOOL_CATEGORY

_POLL_TIMEOUT_SECONDS = 0.2
"""How often the wait loop re-checks `_active_cancel_event()` and `_user_msg_event()` between
polls of the completion queue."""


class WaitForSubagentTool(UserInterruptibleTool):
    """Blocks the calling session's dispatch thread, polling subagent_tracker until completed
    subagent results arrive. Delivers every finished subagent in one call. Fails immediately
    if no subagents are outstanding. An optional timeout parameter caps how long the wait
    runs before raising a retryable ToolInterruptError.
    """

    def name(self) -> str:
        return "WaitForSubagent"

    def aliases(self) -> Sequence[str]:
        return ("WaitForSubagents",)

    def category(self) -> str:
        return SUBAGENT_TOOL_CATEGORY

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Wait for your own subagents to finish and deliver their output. Suspends until at "
            "least one subagent completes its turn, then returns every subagent that has finished "
            "so far. Fails immediately if you have no subagents currently running or awaiting "
            "delivery. An optional `timeout` caps how long to wait."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "timeout": {
                    "type": "number",
                    "description": (
                        "Maximum seconds to wait for a subagent to finish. "
                        "Omit or set to null for no limit."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        timeout: float | None = args.get("timeout")
        context: ToolSetupContext = self.context
        assert context.session is not None
        tracker = context.session.subagent_tracker
        if not tracker.has_undelivered():
            raise ToolCallError(
                "You have no subagents currently running or awaiting delivery.",
                category="validation")
        cancel_event = self._active_cancel_event()
        user_msg_event = self._user_msg_event()
        if user_msg_event is not None:
            user_msg_event.clear()
            # A message queued before this call started must interrupt the wait just like one
            # queued during it; the clear above already consumed its event signal.
            if context.session.pending_queued_message_texts:
                raise ToolInterruptError(reason="new_message")
        elapsed = 0.0
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise ToolInterruptError(reason="user_cancel")
            if user_msg_event is not None and user_msg_event.is_set():
                raise ToolInterruptError(reason="new_message")
            start = time.monotonic()
            handles: list[SubagentHandle] = tracker.pop_all_completed(
                timeout=_POLL_TIMEOUT_SECONDS)
            if handles:
                return {"completed": [_format_completion(handle) for handle in handles]}
            elapsed += time.monotonic() - start
            if timeout is not None and elapsed >= timeout:
                raise ToolInterruptError(
                    reason="timeout",
                    details=(
                        f"{timeout} seconds elapsed but no subagent was ready with a response. "
                        "You may retry to wait longer or do something else in the meantime."
                    ),
                )

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
