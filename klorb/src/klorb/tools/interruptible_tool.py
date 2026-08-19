# © Copyright 2026 Aaron Kimball
"""Base classes for tools whose `apply()` can be interrupted mid-flight by Ctrl+C/Escape."""

import threading

from klorb.tools.tool import Tool


class InterruptibleTool(Tool):
    """A `Tool` whose `apply()` may run long enough that Ctrl+C/Escape should
    interrupt it while it runs.

    Exposes the in-flight turn's cancellation signal so a subclass can poll it during its work
    and return whatever partial result it has.
    """

    def _active_cancel_event(self) -> threading.Event | None:
        """The in-flight turn's cancellation signal, or `None` if not cancellable."""
        session = self.context.session
        return session.active_cancel_event if session is not None else None


class UserInterruptibleTool(InterruptibleTool):
    """An `InterruptibleTool` that also watches for user messages
    enqueued during its blocking wait."""

    def _user_msg_event(self) -> threading.Event | None:
        """The user-message signal, or `None` if not attached to a real `Session`."""
        session = self.context.session
        return session._user_msg_event if session is not None else None
