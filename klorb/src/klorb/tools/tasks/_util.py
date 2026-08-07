# © Copyright 2026 Aaron Kimball
"""Shared plumbing behind the session's "current tracked task" mechanism -- the standing
interjection that reminds the model of it on every turn, and the steps that put a task into
that role -- used by `TodoNext` when it picks or re-returns a task, and by `TodoUpdate`/
`TodoCreate`'s auto-activation (`maybe_activate_task`) so a task can become the current one as a
side effect of editing or creating it, without a separate `TodoNext` call. Also the per-agent
task-claiming logic (`claim_next_ready_task`) `TodoNext` uses to pick only a ready issue this
agent is actually eligible for. See docs/specs/chainlink-task-tracking.md.
"""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from klorb.tools.tasks.common import (
    ALL_LABEL,
    ChainlinkClient,
    ChainlinkError,
    agent_label,
    open_blocker_count,
)

if TYPE_CHECKING:
    from klorb.session import Session
    from klorb.tools.setup_context import ToolSetupContext

logger = logging.getLogger(__name__)

STANDING_INTERJECTION_SUBJECT = "ChainlinkCurrentTask"


def standing_interjection_provider(
    session: "Session", context: "ToolSetupContext",
) -> Callable[[], str | None]:
    """Build the `Session.register_standing_interjection` provider for the current tracked
    task: a message naming its id and title, re-resolved fresh on every poll (`Session.
    cur_chainlink_task_id` only stores the id, not the title), for as long as one is set --
    `None` once it's cleared (no ready or open work left)."""

    def provider() -> str | None:
        task_id = session.cur_chainlink_task_id
        if task_id is None:
            return None
        try:
            issue = ChainlinkClient(context).show_issue(task_id)
        except (ChainlinkError, ValueError):
            return None
        title = issue.get("title", "(untitled)")
        return (
            f'Your current tracked task is #{task_id}: "{title}". Record a comment on it '
            "(TodoUpdate add_comment) whenever you make meaningful progress, learn something "
            "new, or make a decision; close it (TodoUpdate close) once it's done and verified -- "
            "doing so automatically picks up whatever's next."
        )

    return provider


def set_current_task(
    session: "Session", context: "ToolSetupContext", task_id: int | None,
) -> None:
    """Set `task_id` (or clear, if `None`) as the session's current tracked task, and, whenever
    it's not `None`, (re)register the standing interjection reminding the model of it on every
    subsequent turn -- the exact two steps `TodoNext` performs when it hands back a task, shared
    here so `TodoUpdate`/`TodoCreate`'s auto-activation (`maybe_activate_task`) can perform the
    same two steps without duplicating them."""
    session.set_chainlink_task(task_id)
    if task_id is not None:
        session.register_standing_interjection(
            STANDING_INTERJECTION_SUBJECT, standing_interjection_provider(session, context))


def claim_next_ready_task(
    client: ChainlinkClient, ready: list[dict[str, Any]], own_id: str,
) -> dict[str, Any] | None:
    """Pick the next ready issue this agent (`own_id`) may work on: whichever ready issue
    already carries this agent's own `agent_label(own_id)` (in `ready`'s given order), or --
    failing that -- the first `ALL_LABEL`-labeled ready issue this agent successfully claims (see
    `_claim_one`). Returns `None` if nothing is both ready and eligible: every ready issue is
    labeled for a different agent, or every `ALL_LABEL` candidate was already claimed by another
    agent first. `ready` is expected to already be sorted (`issue_sort_key`)
    and every issue in it to carry a `labels` list (i.e. fetched via
    `ChainlinkClient.fetch_and_sort_issues`, never the bare `list_issues` shape)."""
    own_label = agent_label(own_id)
    for issue in ready:
        if own_label in issue.get("labels", []):
            return issue
    for issue in ready:
        if ALL_LABEL not in issue.get("labels", []):
            continue
        claimed = _claim_one(client, issue["id"], own_label)
        if claimed is not None:
            return claimed
    return None


def _claim_one(client: ChainlinkClient, issue_id: int, own_label: str) -> dict[str, Any] | None:
    """Attempt to claim one `ALL_LABEL`-labeled issue under `own_label`. Removing `ALL_LABEL` is
    the compare-and-swap: `ChainlinkClient.remove_label`'s return value tells us whether this
    call is the one that actually removed it, so only the winner of a same-instant race with
    another agent goes on to add `own_label` -- the loser returns `None` immediately, no retry.
    See docs/specs/chainlink-task-tracking.md's "Task assignment" section."""
    issue = client.show_issue(issue_id)
    if ALL_LABEL not in issue.get("labels", []):
        return None
    if not client.remove_label(issue_id, ALL_LABEL):
        return None
    client.add_label(issue_id, own_label)
    return client.show_issue(issue_id)


def task_is_ready(client: ChainlinkClient, task: dict[str, Any]) -> bool:
    """Whether `task` (a full `issue show` detail) has zero still-open blockers -- the same
    readiness test `TodoNext`/`fetch_and_sort_issues` apply, computed for one already-fetched
    task at a time rather than the full fetch-enrich-sort pipeline over every issue."""
    if not task.get("blocked_by"):
        return True
    open_ids = {issue["id"] for issue in client.list_issues(status="open")}
    return open_blocker_count(task, open_ids) == 0


def maybe_activate_task(
    session: "Session", context: "ToolSetupContext", client: ChainlinkClient,
    task: dict[str, Any], *, activate: bool | None,
) -> str | None:
    """Pick up `task` as the session's current tracked task exactly as `TodoNext` would --
    shared by `TodoUpdate`/`TodoCreate` so a model that just edited or created a task doesn't
    need a separate `TodoNext` call to start tracking it. Returns the `active_task_note` string
    to fold into the tool's result if activation happened, else `None`.

    `activate=False` never activates. Otherwise, `task` must be open -- this alone rules out
    activating a task a `TodoUpdate` call just closed in the same call, since `task` there is the
    *post*-close detail. `activate=True` then activates unconditionally past that. `activate=
    None` ("auto mode") activates only if, in addition, the session has no current tracked task
    already and `task` has zero still-open blockers (`task_is_ready`) -- the same state `TodoNext`
    itself requires before ever handing a task back.

    If `task` still carries `ALL_LABEL` (unclaimed) at this point, activation also claims it for
    this session (`_claim_one`, the same claim `TodoNext` performs for a ready `ALL_LABEL` issue)
    before setting it current -- otherwise it would stay up for grabs for another agent's
    `TodoNext` even while this session already treats it as its own current task. `task["labels"]`
    is updated in place to reflect the claim, so a caller returning `task` as its own result (e.g.
    `TodoCreateTool.apply()`) doesn't hand back stale label state. If the claim is lost to a
    same-instant race, activation doesn't happen at all -- `task` isn't really this session's to
    track.
    """
    if activate is False or task.get("status") != "open":
        return None
    if activate is None:
        if session.cur_chainlink_task_id is not None or not task_is_ready(client, task):
            return None
    if ALL_LABEL in task.get("labels", []):
        claimed = _claim_one(client, task["id"], agent_label(session.id))
        if claimed is None:
            return None
        task["labels"] = claimed["labels"]
    set_current_task(session, context, task["id"])
    logger.debug("Auto-activated task #%d as the session's current tracked task.", task["id"])
    return (
        f'Task #{task["id"]} ("{task.get("title", "")}") is now your current tracked task. '
        "Comment on it as you make progress and close it (TodoUpdate close) once it's done "
        "and verified, then call TodoNext for what's next."
    )
