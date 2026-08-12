# © Copyright 2026 Aaron Kimball
"""Runtime bookkeeping for the subagents one `Session` has directly created: live handles, a
FIFO of completions awaiting delivery to that session, and the concurrency accounting
`CreateSubagent`/`MessageSubagent`'s `maxConcurrentPerParent`/`maxActiveTotal` checks need. See
docs/specs/subagents.md's "Subagent lifecycle" and "Communicating back to the parent" sections.
"""

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from klorb.session.mixins.turns import wrap_system_interjection

if TYPE_CHECKING:
    from klorb.session import Session

logger = logging.getLogger(__name__)

SUBAGENT_INTERJECTION_SUBJECT = "subagent"
"""`SystemInterjection subject=` value the subagent-output relay uses -- both the standing
provider (`build_subagent_interjection_provider`) and `cascade_close_subagents`' direct-append
fallback."""

SUBAGENT_MGMT_TOOL_NAMES = frozenset({"CreateSubagent", "WaitForSubagent", "MessageSubagent"})
"""The three subagent-management tools -- omitted from a subagent's own effective tool set
whenever its role's `AgentDefinition.allow_subagents` is `False`. See docs/specs/subagents.md."""

SUBAGENT_ABORTED_MARKER = "(Subagent turn aborted by user)"
"""Appended to a subagent's relayed output by `klorb.agents.policy._run_subagent_turn` when its
turn is cancelled mid-stream -- also the signal `klorb.tui.mixins.subagents_panel.
SubagentsPanelMixin` checks (`marker in handle.output`) to show "Subagent interrupted." in place
of the ordinary "Subagent task complete." notice once a cancelled subagent's handle finishes."""

AGENT_GROUP_INTERJECTION_SUBJECT = "AgentGroup"
"""`SystemInterjection subject=` value the AgentGroup standing interjection uses to notify
subagents of group membership and activity changes."""

_SHUTDOWN_JOIN_TIMEOUT_SECONDS = 5.0
"""How long `cascade_close_subagents` waits for a still-running subagent's background thread to
notice `cancel_event` and finish, before giving up and relaying a termination note without it --
a graceful shutdown must not hang indefinitely on a wedged subagent turn."""

SubagentState = Literal["running", "finished"]
"""A subagent's own lifecycle state: `"running"` while its background turn is actively
processing (consuming a `maxConcurrentPerParent`/`maxActiveTotal` slot), `"finished"` once that
turn ends -- a finished subagent sits dormant (its `Session` never destroyed, per docs/specs/
subagents.md) until `MessageSubagent` resumes it, which flips it back to `"running"`."""


@dataclass
class SubagentHandle:
    """One subagent session a `Session` has directly created, tracked for as long as this
    process runs -- a subagent session is never destroyed once finished, so this handle outlives
    any one turn."""

    session: "Session"
    thread: threading.Thread
    cancel_event: threading.Event
    role: str
    title: str
    state: SubagentState = "running"
    delivered: bool = False
    """Whether this subagent's completed output has already been handed to the creating
    session -- via `WaitForSubagent`, the standing interjection relay, or (at shutdown)
    `cascade_close_subagents`'s direct-append fallback. Distinct from `state`: a `"finished"`
    subagent is not yet `delivered` until one of those three actually consumes it, and stays
    dormant (not counted by `running_count()`) either way until `MessageSubagent` resumes it."""
    output: str | None = None
    """Set once `state == "finished"`: the subagent's conversational output, or a placeholder
    noting an empty, aborted, or terminated turn."""
    parent_interested: bool = True
    """Whether the session that dispatched the turn this handle currently represents was the
    parent itself (`CreateSubagent`/`MessageSubagent` -- the only producers of `True`), versus a
    human addressing this subagent directly on a dormant turn, bypassing the parent
    (`klorb.agents.policy.dispatch_direct_message`'s fresh-turn branch, the only producer of
    `False`). Fixed at construction: a human's mid-turn interjection into an already-running,
    parent-dispatched turn does not change it. `mark_finished` only queues a completion for
    parent delivery when this is `True` -- see docs/specs/subagents.md's "Direct user messaging"
    section."""


def _format_relay_body(handle: SubagentHandle) -> str:
    """Render `handle`'s completed output as the body a `SystemInterjection subject="subagent"`
    tag carries: the `id`/`role`/`title` metadata lines, a blank line, then the subagent's own
    output. See docs/specs/subagents.md's "Communicating back to the parent" section for the
    exact template."""
    assert handle.output is not None
    return (
        f"id: {handle.session.id}\n"
        f"role: {handle.role}\n"
        f"title: {handle.title}\n\n"
        f"{handle.output}"
    )


class SubagentTracker:
    """Owns one `Session`'s bookkeeping for the subagents it has directly created: a
    `SubagentHandle` per child (keyed by the child's own `id`), a FIFO of parent-interested
    handles that have finished and are awaiting delivery, and the concurrency count
    `CreateSubagent`/`MessageSubagent` check against `tools.subagents.maxConcurrentPerParent`.

    Constructed once per `Session` (`SessionCoreMixin.__init__`) and never shared -- counting
    a whole session *tree* (`maxActiveTotal`, see `total_active_subagents`) means walking every
    session's own tracker, not just one.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
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

    def mark_finished(self, child_id: str, output: str) -> None:
        """Record `child_id`'s background turn as done -- called by the background thread
        `dispatch_subagent_turn` runs, once the child's own `send_turn()` call returns (or is
        aborted/fails). Only queues it for delivery to the parent when `handle.parent_interested`
        is `True`; an uninterested completion (a human messaged this subagent directly) still gets
        `state`/`output` set, so it stays visible to the panel and a later `MessageSubagent`, but
        is never handed to `WaitForSubagent`."""
        with self._lock:
            handle = self._handles[child_id]
            handle.state = "finished"
            handle.output = output
            interested = handle.parent_interested
        if interested:
            self._completion_queue.put(handle)
        logger.debug(
            "Subagent %s finished (parent_interested=%s); %s", child_id, interested,
            "queued for delivery" if interested else "not queued (human-addressed turn)")

    def running_count(self) -> int:
        """Subagents this session directly created whose turn is actively processing right now
        -- what `tools.subagents.maxConcurrentPerParent` bounds. A subagent session is never
        destroyed once finished (see docs/specs/subagents.md), so a `"finished"` (dormant,
        possibly still undelivered) subagent does *not* count here -- only `MessageSubagent`
        turning a dormant one back into a running one consumes a concurrency slot again."""
        with self._lock:
            return sum(1 for handle in self._handles.values() if handle.state == "running")

    def has_undelivered(self) -> bool:
        """Whether this session has at least one *parent-interested* subagent still running or
        awaiting delivery -- `WaitForSubagent` fails immediately, rather than suspending forever,
        when this is `False`. An uninterested (human-addressed) handle is never delivered to
        anyone, so it's excluded here regardless of its own `delivered` value -- otherwise this
        would report outstanding work forever for a subagent nothing will ever pop."""
        with self._lock:
            return any(
                handle.parent_interested and not handle.delivered
                for handle in self._handles.values())

    def pop_next_completed(self, timeout: float) -> SubagentHandle | None:
        """Block up to `timeout` seconds for the next completed-but-undelivered, parent-interested
        subagent (oldest first), marking it delivered before returning it, or return `None` on
        timeout. Used by `WaitForSubagent`'s poll loop -- a short `timeout` so it can also check
        for cancellation between waits, mirroring `BashTool`'s persistent-shell poll loop.

        Pops the `SubagentHandle` object the completion queue itself carries, rather than
        re-resolving `child_id` through `self._handles` -- `register()` replaces that dict entry
        on every dispatch (including a later human message to the same subagent), so a re-lookup
        here could return a different, still-running handle than the one that actually finished
        and was queued."""
        try:
            handle = self._completion_queue.get(timeout=timeout)
        except queue.Empty:
            return None
        with self._lock:
            handle.delivered = True
            return handle

    def try_pop_completed(self) -> SubagentHandle | None:
        """Pop and mark delivered at most one completed-but-undelivered, parent-interested
        subagent, without blocking, or return `None` if none are waiting. See `pop_next_completed`
        for why this pops the queued `SubagentHandle` directly rather than re-resolving by id."""
        try:
            handle = self._completion_queue.get_nowait()
        except queue.Empty:
            return None
        with self._lock:
            handle.delivered = True
            return handle

    def pop_all_completed(self, timeout: float) -> list[SubagentHandle]:
        """Block up to `timeout` seconds for at least one completed-but-undelivered subagent,
        then drain every *other* one already waiting (without blocking further), returning all
        of them, oldest first -- or an empty list if none arrived within `timeout`. Used by
        `WaitForSubagent` so several subagents that finished before the caller got around to
        waiting are all delivered in one call, rather than forcing a separate `WaitForSubagent`
        round trip per completion."""
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
    returns `None` if none are waiting. Registered by `CreateSubagentTool.apply()` under
    `SUBAGENT_INTERJECTION_SUBJECT`, so it's polled by every future `send_turn()`/tool-call
    round on the creating session -- see `klorb.session.mixins.core.
    register_standing_interjection`."""
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
    *entire* session tree `session` belongs to -- what `tools.subagents.maxActiveTotal` bounds,
    regardless of which agent in the tree created each one. See `SubagentTracker.running_count`
    for what "running" means -- a dormant, finished-but-undelivered subagent doesn't count."""
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
    """One session in a walk of an entire session tree (`walk_session_tree`): `handle` is the
    `SubagentHandle` its creating session tracks it under, or `None` for the tree's own root
    (which no `SubagentHandle` describes, since nothing created it)."""

    session: "Session"
    handle: SubagentHandle | None
    depth: int


def walk_session_tree(root: "Session") -> list[SessionTreeNode]:
    """Pre-order walk of `root` and every subagent beneath it (at any depth), in creation order
    at each level. `root` itself is always first, with `handle=None`; mirrors the recursive
    walk `total_active_subagents`/`cascade_close_subagents` already do, but collecting every node
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
    tree rooted at its top-level session), or `None` if no such agent exists in the group. Used
    by `TodoCreate`'s `assign_to` validation to resolve a target agent id to a live `Session`
    whose role capabilities can then be checked."""
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
    note) directly into `session`'s own message history before returning. See
    docs/specs/subagents.md's "Persistence" section.

    Called from `Session.close()`, before its own `_finalize_session_persistence()`, so the
    relayed note lands in `messages` before `session.json` is written -- a subagent's own
    conversation is never separately persisted (see that section), so this is the only
    opportunity to keep the parent's persisted transcript from citing a `WaitForSubagent` that
    can no longer resolve.
    """
    for handle in session.subagent_tracker.handles():
        cascade_close_subagents(handle.session)
        if handle.state == "running":
            handle.cancel_event.set()
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
            handle.delivered = True
        handle.session.close()
