# © Copyright 2026 Aaron Kimball
"""`InterruptibleTool`: a `Tool` base class for tools whose `apply()` can run long enough that a
user's Ctrl+C/Escape should reach it mid-flight rather than only at the next tool-call boundary.

`UserInterruptibleTool`: extends `InterruptibleTool` for tools that block waiting for external
events (e.g. `WaitForSubagentTool`) and should also be interrupted when the user sends a new
message during the wait.
"""

import threading

from klorb.tools.tool import Tool


class InterruptibleTool(Tool):
    """A `Tool` whose `apply()` may run long enough — a recursive search over a large tree, a
    long-running subprocess — that a user's Ctrl+C/Escape should interrupt it *while it runs*,
    not only once `Session._run_tool_calls` re-checks for cancellation at the next tool-call
    boundary. It exposes the in-flight turn's cancellation signal so a subclass can poll it during
    its work and stop promptly, returning whatever partial result it has.

    Subclasses today: `klorb.tools.bash.BashTool` (which forwards the signal to its subprocess as
    `SIGINT`), `klorb.tools.grep.GrepTool`, and `klorb.tools.find_file.FindFileTool` (which check
    it between directories/files as they walk the tree). This base class holds only the shared
    plumbing for *reaching* that signal — how each tool reacts to it stays in the tool.
    """

    def _active_cancel_event(self) -> threading.Event | None:
        """The in-flight turn's cancellation signal (`Session.active_cancel_event` — set for as
        long as `Session._dispatch_turn` is running, which includes this very call, since a
        `Tool.apply()` runs synchronously on `_dispatch_turn`'s own thread), or `None` when this
        tool wasn't built from a real `Session` (e.g. a caller that constructs a `ToolSetupContext`
        directly, as most unit tests do) — in which case the work simply isn't cancellable. A
        subclass polls this during its work so a Ctrl+C/Escape interrupt takes effect immediately
        rather than only at the next tool-call/round boundary `Session` itself checks."""
        session = self.context.session
        return session.active_cancel_event if session is not None else None


class UserInterruptibleTool(InterruptibleTool):
    """An `InterruptibleTool` that additionally watches for user messages enqueued during its
    blocking wait. `Session.enqueue_queued_message()` sets `Session._user_msg_event` when the
    user types a new message while a turn is in flight; a `UserInterruptibleTool` subclass polls
    that event alongside the cancel event so the wait is interrupted immediately rather than
    only once the next tool-call boundary re-checks for cancellation.

    Subclass today: `klorb.tools.subagents.wait.WaitForSubagentTool`.
    """

    def _user_msg_event(self) -> threading.Event | None:
        """`Session._user_msg_event`, or `None` when this tool wasn't built from a real
        `Session`. Set by `Session.enqueue_queued_message()` whenever a user message is queued
        during an active turn."""
        session = self.context.session
        return session._user_msg_event if session is not None else None
