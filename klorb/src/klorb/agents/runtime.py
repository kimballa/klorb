# © Copyright 2026 Aaron Kimball
"""Runtime bookkeeping for the subagents one `Session` has directly created: live handles, a
FIFO of completions awaiting delivery to that session, and the concurrency accounting
`CreateSubagent`/`SendMessage`'s `maxConcurrentPerParent`/`maxActiveTotal` checks need.
See docs/specs/subagents.md."""

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from klorb.tools.response_envelope import wrap_system_interjection

if TYPE_CHECKING:
    from klorb.session import Session

logger = logging.getLogger(__name__)

SUBAGENT_INTERJECTION_SUBJECT = "subagent"
"""`SystemInterjection subject=` value the subagent-output relay uses."""

SUBAGENT_MGMT_TOOL_NAMES = frozenset({"CreateSubagent", "WaitForSubagent"})
"""The two subagent-lifecycle tools gated behind a child role's own `allow_subagents`.
`SendMessage`/`GetMessages` (`klorb.tools.subagents.send_message`/`get_messages`) are not gated
this way -- see docs/specs/subagents.md's "Agent-to-agent messaging" section."""

SUBAGENT_ABORTED_MARKER = "(Subagent turn aborted by user)"
"""Appended to a subagent's relayed output when its turn is cancelled mid-stream."""

AGENT_GROUP_INTERJECTION_SUBJECT = "AgentGroup"
"""`SystemInterjection subject=` value the AgentGroup standing interjection uses to notify
subagents of group membership and activity changes."""

_SHUTDOWN_JOIN_TIMEOUT_SECONDS = 2.0
"""How long `cascade_close_subagents` waits, per subagent, for a still-running background thread
to notice `cancel_event` and finish, before giving up and relaying a termination note without
it. Every running subagent in the tree is signaled before any join, so this bounds one
straggler's wait rather than stacking across siblings."""

SubagentState = Literal["running", "finished"]
"""A subagent's own lifecycle state: `"running"` while its background turn is actively
processing (consuming a `maxConcurrentPerParent`/`maxActiveTotal` slot), `"finished"` once that
turn ends."""


@dataclass(frozen=True)
class SubagentTurnOutcome:
    """What one subagent turn produced: the text to deliver to the creating session, and whether
    the turn chain ended normally rather than stopping on a veto, abort, or exception."""

    output: str
    completed: bool


@dataclass
class SubagentHandle:
    """One subagent session a `Session` has directly created, tracked for as long as this
    process runs."""

    session: "Session"
    thread: threading.Thread
    cancel_event: threading.Event
    role: str
    title: str
    delivered: bool = False
    """Whether this subagent's completed output has already been handed to the creating
    session."""
    outcome: SubagentTurnOutcome | None = None
    """`None` while running; set once, as a single atomic attribute write, when this subagent's
    turn ends. `state`/`output` derive from this pair so a reader never observes `state ==
    "finished"` alongside a stale or absent `output`."""
    parent_interested: bool = True
    """Whether the session that dispatched the turn this handle currently represents was the
    parent itself, versus a human addressing this subagent directly on a dormant turn,
    bypassing the parent. Fixed at construction: a human's mid-turn interjection into an
    already-running, parent-dispatched turn does not change it. `mark_finished` only queues a
    completion for parent delivery when this is `True`."""

    @property
    def state(self) -> SubagentState:
        """This subagent's lifecycle state, derived from `outcome`."""
        return "finished" if self.outcome is not None else "running"

    @property
    def output(self) -> str | None:
        """The subagent's conversational output once `state == "finished"`, or `None` while
        still running."""
        return self.outcome.output if self.outcome is not None else None


def _format_relay_body(handle: SubagentHandle) -> str:
    """Render `handle`'s completed output as the body a `SystemInterjection subject="subagent"`
    tag carries: the `id`/`role`/`title` metadata lines, a blank line, then the subagent's own
    output."""
    assert handle.outcome is not None
    return (
        f"id: {handle.session.id}\n"
        f"role: {handle.role}\n"
        f"title: {handle.title}\n\n"
        f"{handle.outcome.output}"
    )


class SubagentTracker:
    """Owns one `Session`'s bookkeeping for the subagents it has directly created: a
    `SubagentHandle` per child (keyed by the child's own `id`), a FIFO of parent-interested
    handles that have finished and are awaiting delivery, and the concurrency count
    `CreateSubagent`/`SendMessage` check against `tools.subagents.maxConcurrentPerParent`.

    Constructed once per `Session` and never shared.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._dispatch_lock = threading.Lock()
        self._handles: dict[str, SubagentHandle] = {}
        self._completion_queue: "queue.Queue[SubagentHandle]" = queue.Queue()

    def register(self, handle: SubagentHandle) -> None:
        """Record a newly-created subagent, before its background turn starts running."""
        with self._lock:
            self._handles[handle.session.id] = handle
        logger.debug("Registered subagent %s (role=%s)", handle.session.id, handle.role)

    def handles(self) -> list[SubagentHandle]:
        """Every subagent this session has ever directly created, in creation order."""
        with self._lock:
            return list(self._handles.values())

    def current_handle(self, child_id: str) -> SubagentHandle | None:
        """The tracker's live entry for `child_id` right now, or `None` if it's never registered
        one. `dispatch_subagent_turn` replaces a resumed subagent's entry with a new
        `SubagentHandle` object rather than mutating the old one in place, so a handle obtained
        earlier can go stale the moment it's released back to the caller."""
        with self._lock:
            return self._handles.get(child_id)

    def dispatch_guard(self) -> threading.Lock:
        """The lock guarding this session's "check a subagent's current state, then dispatch or
        enqueue into its turn" decisions, so two concurrent callers can't both see the same
        dormant subagent as free to resume."""
        return self._dispatch_lock

    def mark_finished(self, child_id: str, outcome: SubagentTurnOutcome) -> None:
        """Record `child_id`'s background turn as done, publishing `outcome` in a single atomic
        attribute write so no reader can observe `state == "finished"` with a stale `output`."""
        with self._lock:
            handle = self._handles[child_id]
            handle.outcome = outcome
            interested = handle.parent_interested
        if interested:
            self._completion_queue.put(handle)
        logger.debug(
            "Subagent %s finished (parent_interested=%s); %s", child_id, interested,
            "queued for delivery" if interested else "not queued (human-addressed turn)")

    def mark_delivered(self, handle: SubagentHandle) -> None:
        """Mark `handle` delivered under this tracker's lock, for a delivery path that doesn't go
        through the completion queue."""
        with self._lock:
            handle.delivered = True

    def try_claim_for_relay(self, child_id: str) -> SubagentHandle | None:
        """Atomically check-and-mark `child_id`'s finished, parent-interested handle delivered.
        Returns `None`, without marking anything, if there's no such handle, it isn't
        parent-interested, it hasn't finished yet, or it's already marked delivered.

        The caller must call `release_relay_claim` on the returned handle if it turns out not to
        actually deliver the output anywhere. Skipping that call permanently hides the handle from
        `WaitForSubagent`, `pop_next_completed`, and `try_pop_completed`, even though nothing ever
        delivered it.

        This shares the tracker's own `_lock` with `pop_next_completed`/`try_pop_completed`'s own
        check-and-set, so at most one of these three methods can claim a given handle."""
        with self._lock:
            handle = self._handles.get(child_id)
            if (handle is None or not handle.parent_interested or handle.delivered
                    or handle.output is None):
                return None
            handle.delivered = True
            return handle

    def release_relay_claim(self, handle: SubagentHandle) -> None:
        """Reverse a `try_claim_for_relay` call: set `handle.delivered` back to `False`. Call this
        when a `try_claim_for_relay` caller decides it will not deliver `handle`'s output anywhere,
        so `WaitForSubagent`/`pop_next_completed`/`try_pop_completed` can still return it later."""
        with self._lock:
            handle.delivered = False

    def mark_parent_interested(self, child_id: str) -> None:
        """Set `child_id`'s current handle's `parent_interested` to `True` under this tracker's
        lock -- called whenever an agent message from this session's own `parent` actually
        reaches `child_id`, so that child's eventual completion is delivered back here even
        though nothing re-dispatched its turn to produce that effect."""
        with self._lock:
            handle = self._handles.get(child_id)
            if handle is not None:
                handle.parent_interested = True

    def running_count(self) -> int:
        """Subagents this session directly created whose turn is actively processing right now.
        A subagent session is never destroyed once finished, so a `"finished"` (dormant,
        possibly still undelivered) subagent does *not* count here."""
        with self._lock:
            return sum(1 for handle in self._handles.values() if handle.state == "running")

    def has_undelivered(self) -> bool:
        """Whether this session has at least one *parent-interested* subagent still running or
        awaiting delivery. An uninterested (human-addressed) handle is never delivered to
        anyone, so it's excluded here regardless of its own `delivered` value."""
        with self._lock:
            return any(
                handle.parent_interested and not handle.delivered
                for handle in self._handles.values())

    def pop_next_completed(self, timeout: float) -> SubagentHandle | None:
        """Block up to `timeout` seconds for the next completed-but-undelivered, parent-interested
        subagent (oldest first), marking it delivered before returning it, or return `None` on
        timeout. A handle already marked delivered by another caller by the time it's popped is
        skipped rather than returned again."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                handle = self._completion_queue.get(timeout=remaining)
            except queue.Empty:
                return None
            with self._lock:
                if handle.delivered:
                    continue
                handle.delivered = True
                return handle

    def try_pop_completed(self) -> SubagentHandle | None:
        """Pop and mark delivered at most one completed-but-undelivered, parent-interested
        subagent, without blocking, or return `None` if none are waiting. A handle already marked
        delivered by another caller by the time it's popped is skipped rather than returned
        again."""
        while True:
            try:
                handle = self._completion_queue.get_nowait()
            except queue.Empty:
                return None
            with self._lock:
                if handle.delivered:
                    continue
                handle.delivered = True
                return handle

    def pop_all_completed(self, timeout: float) -> list[SubagentHandle]:
        """Block up to `timeout` seconds for at least one completed-but-undelivered subagent,
        then drain every *other* one already waiting (without blocking further), returning all
        of them, oldest first."""
        first = self.pop_next_completed(timeout=timeout)
        if first is None:
            return []
        drained = [first]
        while True:
            more = self.try_pop_completed()
            if more is None:
                return drained
            drained.append(more)


def build_subagent_interjection_provider(tracker: "SubagentTracker") -> Callable[[], str | None]:
    """Build the zero-arg closure `Session.register_standing_interjection` expects: pops at
    most one completed-but-undelivered subagent from `tracker` and formats its relay body, or
    returns `None` if none are waiting."""
    def provider() -> str | None:
        handle = tracker.try_pop_completed()
        if handle is None:
            return None
        return _format_relay_body(handle)
    return provider


def build_agent_group_interjection_provider(
    session: "Session",
) -> Callable[[], str | None]:
    """Build a standing interjection provider that emits an `AgentGroup` table whenever the
    session tree's composition or subagent activity changes. Each call walks the full tree
    rooted at `session`'s top-level session, builds a markdown table of every agent (role, id,
    title, state), and compares it against the last-emitted snapshot. Returns the table on the
    first call (establishing the baseline) and again whenever the frozenset of
    ``(session_id, role_name, state)`` tuples differs from the cached snapshot; returns `None`
    (no interjection) when the group is unchanged."""
    last_snapshot: frozenset[tuple[str, str, str]] = frozenset()

    def _build_group_table(
        session: "Session", root: "Session", nodes: list[SessionTreeNode],
    ) -> str:
        rows = [
            "| Role | Id | Title | State |",
            "| --- | --- | --- | --- |",
        ]
        for node in nodes:
            s = node.session
            state = (
                node.handle.state if node.handle is not None
                else "running"
            )
            title = s.name or ""
            rows.append(f"| {s.config.role_name} | {s.id} | {title} | {state} |")
        return "\n".join(rows)

    def provider() -> str | None:
        nonlocal last_snapshot
        root = session
        while root.parent is not None:
            root = root.parent
        nodes = walk_session_tree(root)
        snapshot = frozenset(
            (node.session.id, node.session.config.role_name,
             node.handle.state if node.handle is not None else "running")
            for node in nodes
        )
        if snapshot == last_snapshot:
            return None
        last_snapshot = snapshot
        logger.debug(
            "AgentGroup interjection: group changed (%d agents), emitting table.",
            len(nodes))
        return _build_group_table(session, root, nodes)

    return provider


def total_active_subagents(session: "Session") -> int:
    """Count every subagent (at any depth) whose turn is actively running right now, across the
    *entire* session tree `session` belongs to."""
    root = session
    while root.parent is not None:
        root = root.parent
    total = 0
    stack = [root]
    while stack:
        current = stack.pop()
        total += current.subagent_tracker.running_count()
        stack.extend(handle.session for handle in current.subagent_tracker.handles())
    return total


@dataclass(frozen=True)
class SessionTreeNode:
    """One session in a walk of an entire session tree: `handle` is the
    `SubagentHandle` its creating session tracks it under, or `None` for the tree's own root
    (which no `SubagentHandle` describes, since nothing created it)."""

    session: "Session"
    handle: SubagentHandle | None
    depth: int


def walk_session_tree(root: "Session") -> list[SessionTreeNode]:
    """Pre-order walk of `root` and every subagent beneath it (at any depth), in creation order
    at each level. `root` itself is always first, with `handle=None`; collecting every node
    rather than only counting or closing them."""
    nodes = [SessionTreeNode(session=root, handle=None, depth=0)]

    def _walk(session: "Session", depth: int) -> None:
        for handle in session.subagent_tracker.handles():
            nodes.append(SessionTreeNode(session=handle.session, handle=handle, depth=depth))
            _walk(handle.session, depth + 1)

    _walk(root, 1)
    return nodes


def find_session_in_group(session: "Session", agent_id: str) -> "Session | None":
    """Return the `Session` with `id == agent_id` anywhere in `session`'s own group (the entire
    tree rooted at its top-level session), or `None` if no such agent exists in the group."""
    root = session
    while root.parent is not None:
        root = root.parent
    for node in walk_session_tree(root):
        if node.session.id == agent_id:
            return node.session
    return None


def cascade_close_subagents(session: "Session") -> None:
    """Recursively close every subagent `session` has directly or indirectly created, deepest
    first, relaying any not-yet-delivered output (or, for one still running, a termination
    note) directly into `session`'s own message history before returning. Every still-running
    subagent anywhere in the tree is signaled to cancel up front, so they unwind concurrently
    instead of waiting their turn behind an earlier sibling's join."""
    for node in walk_session_tree(session):
        if node.handle is not None and node.handle.state == "running":
            node.handle.cancel_event.set()

    for handle in session.subagent_tracker.handles():
        cascade_close_subagents(handle.session)
        if handle.state == "running":
            handle.thread.join(timeout=_SHUTDOWN_JOIN_TIMEOUT_SECONDS)
        if not handle.delivered and handle.parent_interested:
            output = (
                handle.output if handle.output is not None
                else "(Subagent terminated: harness closed before it finished)"
            )
            body = (
                f"id: {handle.session.id}\n"
                f"role: {handle.role}\n"
                f"title: {handle.title}\n\n"
                f"{output}\n\n"
                "The user did not provide a prompt this turn; there is only this system "
                "interjection with the output of a recently-completed subagent."
            )
            session.append_system_note(wrap_system_interjection(SUBAGENT_INTERJECTION_SUBJECT, body))
            session.subagent_tracker.mark_delivered(handle)
        handle.session.close()
