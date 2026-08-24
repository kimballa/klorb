# © Copyright 2026 Aaron Kimball
"""The cross-tree agent-to-agent message queue `SendMessage`/`GetMessages` use to deliver a
message to an agent that isn't ready to receive it immediately: `AgentMessageQueue` is one global
FIFO per session tree, held lazily on the tree's root `Session` and resolved on demand by walking
`.parent`, exactly like `klorb.agents.runtime.find_session_in_group` already does. See
docs/specs/subagents.md."""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from klorb.tools.exceptions import ToolCallError

if TYPE_CHECKING:
    from klorb.session import Session

DEFAULT_MAX_QUEUE_SIZE = 100
"""Fallback queue cap used to size a `Session` constructed without a `ProcessConfig` (e.g. most
unit tests). See `ProcessConfig.messaging_max_queue_size`."""

AGENT_MESSAGE_INTERJECTION_SUBJECT = "AgentMessage"
"""`SystemInterjection subject=` value the standing "you have an unread agent message" reminder
uses."""


@dataclass(frozen=True)
class QueuedAgentMessage:
    """One undelivered `SendMessage` call, still sitting in `AgentMessageQueue`."""

    sender_id: str
    sender_role: str
    recipient_id: str
    body: str


class AgentMessageQueue:
    """One session tree's undelivered agent-to-agent messages, in send order. A single flat FIFO
    rather than a queue per recipient: `klorb.agents.policy.try_wake_next_queued_agent` scans it
    front-to-back so that, when several dormant agents are all waiting for a concurrency slot to
    free up, whichever was messaged first is the one woken first."""

    def __init__(self, max_size: int = DEFAULT_MAX_QUEUE_SIZE) -> None:
        self._lock = threading.Lock()
        self._entries: list[QueuedAgentMessage] = []
        self._max_size = max_size

    def enqueue(self, sender_id: str, sender_role: str, recipient_id: str, body: str) -> None:
        """Append one message to the queue. Raises `ToolCallError` (category `"transient"`) if
        the queue is already at `ProcessConfig.messaging_max_queue_size` -- the fix is to wait
        for something to be delivered and retry, not to change the call's arguments."""
        with self._lock:
            if len(self._entries) >= self._max_size:
                raise ToolCallError(
                    f"The agent-message queue is full ({self._max_size} message(s) pending) -- "
                    "try again once some of them have been delivered.", category="transient")
            self._entries.append(QueuedAgentMessage(sender_id, sender_role, recipient_id, body))

    def has_pending(self, recipient_id: str) -> bool:
        """Whether at least one undelivered message is addressed to `recipient_id`, without
        removing it -- for the standing `AgentMessage` interjection."""
        with self._lock:
            return any(entry.recipient_id == recipient_id for entry in self._entries)

    def pop_all_for(self, recipient_id: str) -> list[QueuedAgentMessage]:
        """Remove and return every message addressed to `recipient_id`, oldest first."""
        with self._lock:
            matching = [e for e in self._entries if e.recipient_id == recipient_id]
            if matching:
                self._entries = [e for e in self._entries if e.recipient_id != recipient_id]
            return matching

    def peek_next_dormant_candidate(self, is_dormant: Callable[[str], bool]) -> str | None:
        """The first distinct `recipient_id` in FIFO order for which `is_dormant` returns `True`
        -- a lock-light hint for `try_wake_next_queued_agent`, which must still re-validate the
        candidate's state under that recipient's own `dispatch_guard()` before acting on it (see
        docs/specs/subagents.md's "Agent-to-agent messaging" section for why). Recipients for
        which `is_dormant` returns `False` (currently running, or not a subagent at all) are
        skipped without ending the scan -- they aren't competing for a concurrency slot."""
        with self._lock:
            seen: set[str] = set()
            for entry in self._entries:
                if entry.recipient_id in seen:
                    continue
                seen.add(entry.recipient_id)
                if is_dormant(entry.recipient_id):
                    return entry.recipient_id
            return None


def get_agent_message_queue(session: "Session") -> AgentMessageQueue:
    """The `AgentMessageQueue` shared by `session`'s entire tree -- every `Session` (root or
    subagent) holds a direct reference to the same instance, constructed once by the tree's root
    at its own construction (see `Session.agent_message_queue`), mirroring how `workspace_indexer`
    is shared down the tree."""
    return session.agent_message_queue


def format_new_turn_message(
    messages: list[QueuedAgentMessage], recipient_parent_id: str | None,
) -> str:
    """Render `messages` (all addressed to the same recipient) as the `role="user"` prompt text
    for the turn that delivers them -- used both when a dormant recipient is woken to receive
    them and when a running recipient's own turn drains them at its end, in place of GetMessages.
    """
    parts = ["You have received the following message(s) from other agents, not the user:", ""]
    for i, message in enumerate(messages, start=1):
        tag = " (your parent)" if message.sender_id == recipient_parent_id else ""
        parts.append(f"{i}. From {message.sender_id}{tag}:\n{message.body}")
    parts.append(
        "Respond to your own work as appropriate. If you want to reply to a sender that isn't "
        "your parent, use SendMessage.")
    return "\n\n".join(parts)
