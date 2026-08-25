# © Copyright 2026 Aaron Kimball
"""`Channel`: the broadcast chat room `PostChat`/`ReadChat` post to and read from. See
docs/specs/subagents.md.
"""

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from klorb.counter import AtomicCounter

if TYPE_CHECKING:
    from klorb.session import Session

DEFAULT_MAX_HISTORY = 2000
"""Fallback retained-message cap for a `Channel` built without a `ProcessConfig`."""

DEFAULT_MAX_MENTION_WAKES = 50
"""Fallback runaway-wake-loop guard threshold for a `Channel` built without a `ProcessConfig`."""

CHAT_UNREAD_INTERJECTION_SUBJECT = "ChatUnread"
"""`SystemInterjection subject=` value the standing unread-chat reminder uses."""

_USER_PARTICIPANT_ID = "user"

_MENTION_TOKEN_RE = re.compile(r"(?<!\S)@([A-Za-z0-9_.-]+)")
"""A start/whitespace-anchored `@token`."""


@dataclass(frozen=True)
class ChatMessage:
    """One posted chat-room message, retained until trimmed by `Channel`'s own `max_history`."""

    seq: int
    """A monotonically increasing sequence number, never reused or renumbered, so a stored
    high-water mark stays valid across a trim."""
    sender_id: str
    timestamp: datetime
    body: str
    """The raw text exactly as posted, never rewritten."""
    mentions: list[str]
    """Participant ids (or `"user"`) `body`'s `@token`s resolved to, against the live session
    tree at post time."""
    unresolved_mentions: list[str]
    """`@token`s in `body` that didn't resolve to any live participant."""


def chat_nickname(session_or_id: "Session | str") -> str:
    """The human-facing nickname for a chat participant: `"user"` unchanged, a live `Session`'s
    `f"{role}-{address}"` form, or the given id itself when it isn't a live session."""
    if isinstance(session_or_id, str):
        return session_or_id
    return f"{session_or_id.config.role_name}-{session_or_id.address()}"


def _resolve_mentions(body: str, resolvable: dict[str, str]) -> tuple[list[str], list[str]]:
    """Extract every `@token` in `body`, resolving each against `resolvable` (token -> canonical
    participant id). Returns `(mentions, unresolved_mentions)`, each de-duplicated in
    first-seen order."""
    mentions: list[str] = []
    unresolved: list[str] = []
    for match in _MENTION_TOKEN_RE.finditer(body):
        token = match.group(1)
        resolved = resolvable.get(token)
        if resolved is not None:
            if resolved not in mentions:
                mentions.append(resolved)
        elif token not in unresolved:
            unresolved.append(token)
    return mentions, unresolved


def _live_mention_targets(session: "Session") -> dict[str, str]:
    """Build a fresh token -> canonical participant id map from `session`'s own live tree, for
    one `Channel.post()` call: every session's raw id and `chat_nickname()` form, plus the
    reserved `"user"` literal."""
    from klorb.agents.runtime import walk_session_tree

    root = session
    while root.parent is not None:
        root = root.parent
    resolvable: dict[str, str] = {_USER_PARTICIPANT_ID: _USER_PARTICIPANT_ID}
    for node in walk_session_tree(root):
        resolvable[node.session.id] = node.session.id
        resolvable[chat_nickname(node.session)] = node.session.id
    return resolvable


class Channel:
    """One session tree's broadcast chat room: every posted `ChatMessage`, plus each
    participant's own read position. Thread-safe, since concurrent agents may call `post`/
    `read_and_advance` from different threads."""

    def __init__(
        self, max_history: int = DEFAULT_MAX_HISTORY,
        max_mention_wakes: int = DEFAULT_MAX_MENTION_WAKES,
    ) -> None:
        self._lock = threading.Lock()
        self._messages: list[ChatMessage] = []
        self._next_seq = AtomicCounter()
        self._hwm: dict[str, int] = {}
        self._mention_wake_count = AtomicCounter()
        self._max_history = max_history
        self._max_mention_wakes = max_mention_wakes
        self._dirty = False

    def post(self, sender_id: str, body: str, session: "Session") -> ChatMessage:
        """Post `body` from `sender_id`, resolving its `@mention`s against `session`'s live
        tree."""
        mentions, unresolved = _resolve_mentions(body, _live_mention_targets(session))
        seq = self._next_seq.increment()
        message = ChatMessage(
            seq=seq, sender_id=sender_id, timestamp=datetime.now(), body=body,
            mentions=mentions, unresolved_mentions=unresolved)
        with self._lock:
            self._messages.append(message)
            if len(self._messages) > self._max_history:
                self._messages = self._messages[-self._max_history:]
            self._hwm[sender_id] = seq
            self._dirty = True
        return message

    def register_participant(self, participant_id: str, at_seq: int | None = None) -> None:
        """Seed a fresh high-water mark for `participant_id`; a no-op if one already exists.
        `at_seq` defaults to the channel's current sequence value, so a newly registered
        participant's hwm starts at "now," not the beginning of the log."""
        seed = at_seq if at_seq is not None else self._next_seq.get_value()
        with self._lock:
            if participant_id not in self._hwm:
                self._hwm[participant_id] = seed
                self._dirty = True

    def unread_count(self, participant_id: str) -> int:
        """How many retained messages `participant_id` hasn't yet read."""
        with self._lock:
            hwm = self._hwm.get(participant_id, 0)
            return sum(1 for message in self._messages if message.seq > hwm)

    def unread_mention_count(self, participant_id: str) -> int:
        """How many of `participant_id`'s unread messages `@mention` it directly."""
        with self._lock:
            hwm = self._hwm.get(participant_id, 0)
            return sum(
                1 for message in self._messages
                if message.seq > hwm and participant_id in message.mentions)

    def read_and_advance(
        self, participant_id: str, limit: int | None = None,
    ) -> list[ChatMessage]:
        """Return `participant_id`'s unread messages, oldest first, capped at `limit` if given.
        This is the only way a participant's hwm advances."""
        with self._lock:
            hwm = self._hwm.get(participant_id, 0)
            unread = [message for message in self._messages if message.seq > hwm]
            if limit is not None:
                unread = unread[:limit]
            if unread:
                self._hwm[participant_id] = unread[-1].seq
                self._dirty = True
            return unread

    def history(self, limit: int | None = None) -> list[ChatMessage]:
        """Every retained message, oldest first, most recent `limit` if given, regardless of
        any participant's hwm."""
        with self._lock:
            messages = list(self._messages)
        return messages[-limit:] if limit is not None else messages

    def participant_ids(self) -> frozenset[str]:
        """Every participant id this channel has ever seeded a high-water mark for."""
        with self._lock:
            return frozenset(self._hwm)

    def mention_wake_count(self) -> int:
        """How many `@mention` active-wake attempts have been made against this channel so
        far."""
        return self._mention_wake_count.get_value()

    def increment_mention_wake_count(self) -> int:
        """Record one more attempted `@mention` active wake and return the new count."""
        return self._mention_wake_count.increment()

    def is_dirty(self) -> bool:
        """Whether this channel has changed since the last `mark_persisted()` call."""
        with self._lock:
            return self._dirty

    def mark_persisted(self) -> None:
        """Clear the dirty flag."""
        with self._lock:
            self._dirty = False

    def snapshot(self) -> tuple[list[ChatMessage], dict[str, int], int, int]:
        """This channel's full persistable state: retained messages, each participant's hwm,
        the next seq to assign, and the mention-wake count."""
        with self._lock:
            return (
                list(self._messages), dict(self._hwm), self._next_seq.get_value(),
                self._mention_wake_count.get_value())

    @classmethod
    def restore(
        cls, messages: list[ChatMessage], hwm: dict[str, int], next_seq: int,
        mention_wake_count: int, *, max_history: int = DEFAULT_MAX_HISTORY,
        max_mention_wakes: int = DEFAULT_MAX_MENTION_WAKES,
    ) -> "Channel":
        """Rebuild a `Channel` from previously persisted state. A restored hwm entry for a
        session id that never reappears is simply inert."""
        channel = cls(max_history=max_history, max_mention_wakes=max_mention_wakes)
        channel._messages = list(messages)
        channel._hwm = dict(hwm)
        channel._next_seq = AtomicCounter(next_seq)
        channel._mention_wake_count = AtomicCounter(mention_wake_count)
        return channel


def get_chat_channel(session: "Session") -> Channel:
    """The `Channel` shared by `session`'s entire tree."""
    return session.chat_channel


def build_chat_unread_interjection_provider(session: "Session") -> Callable[[], "str | None"]:
    """Build the standing `ChatUnread` interjection provider for `session`: reminds it to call
    `ReadChat` when it has unread chat-room messages, and separately flags how many `@mention`
    it directly."""

    def provider() -> str | None:
        channel = session.chat_channel
        unread = channel.unread_count(session.id)
        if unread == 0:
            return None
        message = f"You have {unread} unread chat room message(s). Call ReadChat to see them."
        mentioned = channel.unread_mention_count(session.id)
        if mentioned:
            message += f" This includes {mentioned} that @mention you directly."
        return message

    return provider
