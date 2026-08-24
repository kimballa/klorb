# © Copyright 2026 Aaron Kimball
"""Tests for klorb.agents.policy: the CreateSubagent rejection checks and the tool/skill/role
intersection wiring that produces a subagent's SessionConfig and tool registry.
"""
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from unittest.mock import MagicMock

import pytest
from tools.subagents.conftest import _FakeProvider

from klorb.agents.messaging import get_agent_message_queue
from klorb.agents.policy import (
    compute_root_session_grants,
    dispatch_direct_message,
    dispatch_subagent_turn,
    plan_subagent_creation,
)
from klorb.agents.runtime import SUBAGENT_MGMT_TOOL_NAMES, SubagentHandle, SubagentTurnOutcome
from klorb.api_provider import ProviderResponse
from klorb.hooks.config import HookConfig, TimerEventConfig
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig, TurnEventHandlers
from klorb.session.events import QueuedMessage
from klorb.tools.exceptions import ToolCallError
from klorb.tools.registry import ToolRegistry
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.subagents.wait import WaitForSubagentTool
from klorb.workspace import Workspace


@pytest.fixture(autouse=True)
def _unsandboxed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("klorb.hooks.bash_handler.bwrap_available", lambda: False)


def _operator_context(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig], *,
    max_concurrent: int = 4, max_active: int = 16, max_depth: int = 2,
) -> ToolSetupContext:
    process_config = ProcessConfig(
        subagents_max_concurrent_per_parent=max_concurrent,
        subagents_max_active_total=max_active, subagents_max_depth=max_depth)
    session_config = make_session_config(role_name="operator")
    grants = compute_root_session_grants(process_config, session_config, session_config.role_name)
    session_config.skill_rules = grants.skill_rules
    session = Session(
        session_config, provider=MagicMock(), process_config=process_config,
        tool_registry=grants.tool_registry, effective_subagent_roles=grants.effective_subagent_roles)
    return ToolSetupContext(process_config=process_config, session_config=session_config, session=session)


def _no_child_roles_context(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig], *,
    max_concurrent: int = 4, max_active: int = 16, max_depth: int = 2,
) -> ToolSetupContext:
    process_config = ProcessConfig(
        subagents_max_concurrent_per_parent=max_concurrent,
        subagents_max_active_total=max_active, subagents_max_depth=max_depth)
    session_config = make_session_config(role_name="operator")
    grants = compute_root_session_grants(process_config, session_config, session_config.role_name)
    session_config.skill_rules = grants.skill_rules
    session = Session(
        session_config, provider=MagicMock(), process_config=process_config,
        tool_registry=grants.tool_registry, effective_subagent_roles=frozenset())
    return ToolSetupContext(process_config=process_config, session_config=session_config, session=session)


def test_rejects_when_depth_would_exceed_max_depth(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _operator_context(tmp_path, make_session_config, max_depth=1)
    assert context.session is not None
    context.session.depth = 1  # simulate this session already being one hop below the root

    with pytest.raises(ToolCallError) as exc_info:
        plan_subagent_creation(context, "explorer", None, None)
    assert exc_info.value.category == "validation"
    assert "depth" in str(exc_info.value)


def test_rejects_when_callers_own_role_disallows_subagents(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """A caller whose own `role_name` has no `agents.json` entry at all is rejected."""
    process_config = ProcessConfig()
    session_config = make_session_config(role_name="no_such_role", workspace=Workspace(path=tmp_path))
    tool_registry = ToolRegistry.discover_tools(process_config, session_config)
    session = Session(
        session_config, provider=MagicMock(), process_config=process_config, tool_registry=tool_registry)
    context = ToolSetupContext(process_config=process_config,
                               session_config=session_config, session=session)

    with pytest.raises(ToolCallError, match="may not create subagents"):
        plan_subagent_creation(context, "explorer", None, None)


def test_rejects_unknown_role(tmp_path: Path, make_session_config: Callable[..., SessionConfig]) -> None:
    context = _operator_context(tmp_path, make_session_config)

    with pytest.raises(ToolCallError, match="not among the subagent roles") as exc_info:
        plan_subagent_creation(context, "no_such_role", None, None)
    # The error names the roles the agent may actually retry with.
    assert "explorer" in str(exc_info.value)


def test_operator_cannot_launch_another_operator(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """operator's own agents.json entry names
    `restrict_to.subagent_roles: ["explorer", "reviewer", "planner", "implementer",
    "pair_programmer"]`. `_operator_context` builds its root session via
    `compute_root_session_grants`, so `effective_subagent_roles` is already `{"explorer",
    "implementer", "pair_programmer", "planner", "reviewer"}` by the time
    `plan_subagent_creation` reads it."""
    context = _operator_context(tmp_path, make_session_config)

    with pytest.raises(ToolCallError, match="not among the subagent roles") as exc_info:
        plan_subagent_creation(context, "operator", None, None)
    assert (
        "['explorer', 'implementer', 'pair_programmer', 'planner', 'reviewer']"
        in str(exc_info.value))


def test_informed_cannot_launch_subagents_when_empty_roles_list(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """When the agent's allowed subagent roles list is the empty list, it is explicitly
    told in the CreateSubagent error message that it may not create subagents.
    """
    context = _no_child_roles_context(tmp_path, make_session_config)

    with pytest.raises(ToolCallError, match="may not create subagents") as exc_info:
        plan_subagent_creation(context, "explorer", None, None)
    assert "may not create subagents" in str(exc_info.value)


def test_rejects_role_outside_the_callers_own_effective_subagent_roles(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    process_config = ProcessConfig()
    session_config = make_session_config(role_name="operator", workspace=Workspace(path=tmp_path))
    tool_registry = ToolRegistry.discover_tools(process_config, session_config)
    # Simulate this "operator" session being itself a subagent that was narrowed, at its own
    # creation, to only ever launch "explorer" -- never a fresh agents.json lookup of what
    # "operator" nominally allows.
    session = Session(
        session_config, provider=MagicMock(), process_config=process_config, tool_registry=tool_registry,
        effective_subagent_roles=frozenset({"explorer"}))
    context = ToolSetupContext(process_config=process_config,
                               session_config=session_config, session=session)

    with pytest.raises(ToolCallError, match="not among the subagent roles") as exc_info:
        plan_subagent_creation(context, "operator", None, None)
    assert "['explorer']" in str(exc_info.value)


def test_rejects_when_concurrent_per_parent_limit_already_reached(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _operator_context(tmp_path, make_session_config, max_concurrent=0)

    with pytest.raises(ToolCallError, match="Call WaitForSubagent") as exc_info:
        plan_subagent_creation(context, "explorer", None, None)
    assert exc_info.value.category == "transient"


def test_rejects_when_active_total_limit_already_reached(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _operator_context(tmp_path, make_session_config, max_active=0)

    with pytest.raises(ToolCallError, match="Call WaitForSubagent") as exc_info:
        plan_subagent_creation(context, "explorer", None, None)
    assert exc_info.value.category == "transient"


def test_finished_but_undelivered_subagent_does_not_count_toward_concurrent_limit(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """A subagent session is never destroyed once it finishes its turn."""
    context = _operator_context(tmp_path, make_session_config, max_concurrent=1)
    assert context.session is not None
    child = Session(make_session_config(role_name="explorer"), provider=MagicMock(), parent=context.session)
    handle = SubagentHandle(
        session=child, thread=threading.Thread(target=lambda: None), cancel_event=threading.Event(),
        role="explorer", title="earlier task")
    context.session.subagent_tracker.register(handle)
    context.session.subagent_tracker.mark_finished(
        child.id, SubagentTurnOutcome(output="done", completed=True))

    plan = plan_subagent_creation(context, "explorer", None, None)  # must not raise

    assert plan.session_config.role_name == "explorer"


def test_no_session_is_constructed_when_a_check_fails(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _operator_context(tmp_path, make_session_config, max_depth=0)
    assert context.session is not None
    context.session.depth = 0

    with pytest.raises(ToolCallError):
        plan_subagent_creation(context, "explorer", None, None)

    assert context.session.subagent_tracker.handles() == []


def test_explorer_plan_excludes_subagent_management_tools(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _operator_context(tmp_path, make_session_config)

    plan = plan_subagent_creation(context, "explorer", None, None)

    assert not (set(plan.tool_classes) & SUBAGENT_MGMT_TOOL_NAMES)
    assert "ReadFile" in plan.tool_classes
    assert "Grep" in plan.tool_classes
    # EditFile isn't in the explorer role's restrict_to.tools list at all.
    assert "EditFile" not in plan.tool_classes


def test_explorer_plan_session_config_carries_the_explorer_role(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _operator_context(tmp_path, make_session_config)

    plan = plan_subagent_creation(context, "explorer", None, None)

    assert plan.session_config.role_name == "explorer"
    assert plan.role_definition.default_model == "klorb-default/fast"


def test_plan_subagent_creation_filters_out_non_heritable_hooks_and_events(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _operator_context(tmp_path, make_session_config)
    assert context.session is not None
    context.session.config.hooks = {
        "onToolUse": [
            HookConfig.model_validate({"type": "chat", "prompt": "heritable", "isHeritable": True}),
            HookConfig.model_validate({"type": "chat", "prompt": "not-heritable", "isHeritable": False}),
        ],
    }
    context.session.config.events = {
        "Timer": [
            TimerEventConfig.model_validate({
                "interval_minutes": 5, "action": {"type": "chat", "prompt": "tick"},
                "isHeritable": False,
            }),
        ],
    }

    plan = plan_subagent_creation(context, "explorer", None, None)

    assert [h.prompt for h in plan.session_config.hooks["onToolUse"]] == ["heritable"]
    assert plan.session_config.events == {}
    # The parent's own dicts are untouched.
    assert len(context.session.config.hooks["onToolUse"]) == 2
    assert "Timer" in context.session.config.events


def test_allowed_tools_override_replaces_the_roles_own_list(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _operator_context(tmp_path, make_session_config)

    plan = plan_subagent_creation(context, "explorer", ["FindFile", "SearchSkills"], None)

    # "SearchSkills" isn't in explorer's own restrict_to.tools, but the override still can't
    # grant anything the *parent* (operator, here with the full real tool catalog) doesn't have
    # -- since the parent does have it, and it's read-only, this override is honored despite
    # exceeding the role's own nominal list.
    assert set(plan.tool_classes) == {"FindFile", "SearchSkills"}


def test_allowed_tools_override_still_respects_enforce_readonly_tools(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _operator_context(tmp_path, make_session_config)

    # "Bash" is not read-only; explorer's own enforce_readonly_tools=True isn't touched by the
    # allowed_tools override (only the "tools" field is), so it's still clamped out.
    plan = plan_subagent_creation(context, "explorer", ["FindFile", "Bash"], None)

    assert set(plan.tool_classes) == {"FindFile"}


def test_allowed_tools_override_still_cannot_exceed_the_parents_own_tool_set(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _operator_context(tmp_path, make_session_config)

    plan = plan_subagent_creation(context, "explorer", ["FindFile", "NoSuchTool"], None)

    assert set(plan.tool_classes) == {"FindFile"}


def test_subagent_roles_are_the_explorer_roles_own_restriction_intersected_with_the_parents(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    context = _operator_context(tmp_path, make_session_config)

    plan = plan_subagent_creation(context, "explorer", None, None)

    # explorer's restrict_to.subagent_roles names ["explorer", "vision_assistant"], but
    # "vision_assistant" has no agents.json entry yet (added in a later phase) -- it can't
    # survive the intersection against the operator parent's own effective roles (its own
    # restrict_to.subagent_roles, ["explorer"] -- operator may launch an explorer but not
    # another operator).
    assert plan.effective_subagent_roles == {"explorer"}


def test_dispatch_direct_message_enqueues_into_a_running_turn(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """A human messaging a still-running subagent just enqueues into its current turn."""
    provider = _FakeProvider()
    process_config = ProcessConfig()
    parent = Session(make_session_config(role_name="operator", workspace=Workspace(path=tmp_path)),
                     provider=provider, process_config=process_config)
    child = Session(make_session_config(role_name="explorer"), provider=provider, parent=parent)
    never_finishes = threading.Event()
    handle = SubagentHandle(
        session=child, thread=threading.Thread(target=never_finishes.wait, daemon=True),
        cancel_event=threading.Event(), role="explorer", title="task")
    parent.subagent_tracker.register(handle)
    handle.thread.start()

    try:
        dispatch_direct_message(process_config, child, handle, "keep going, also check lint")
        drained = child.drain_queued_messages()
    finally:
        never_finishes.set()
        handle.thread.join(timeout=5.0)

    assert [m.message_text for m in drained] == ["keep going, also check lint"]
    assert handle.parent_interested is True  # untouched -- this turn was parent-dispatched
    assert parent.subagent_tracker.handles() == [handle]  # no second handle was registered


def test_dispatch_direct_message_starts_a_fresh_uninterested_turn_on_a_dormant_subagent(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    provider = _FakeProvider(reply_text="direct reply")
    process_config = ProcessConfig()
    parent = Session(make_session_config(role_name="operator", workspace=Workspace(path=tmp_path)),
                     provider=provider, process_config=process_config)
    child = Session(make_session_config(role_name="explorer"), provider=provider, parent=parent)
    dormant_handle = SubagentHandle(
        session=child, thread=threading.Thread(target=lambda: None), cancel_event=threading.Event(),
        role="explorer", title="task",
        outcome=SubagentTurnOutcome(output="earlier output", completed=True))
    parent.subagent_tracker.register(dormant_handle)

    dispatch_direct_message(process_config, child, dormant_handle, "poking in directly")

    new_handle = parent.subagent_tracker.handles()[0]
    new_handle.thread.join(timeout=5.0)
    assert new_handle is not dormant_handle
    assert new_handle.parent_interested is False
    assert new_handle.state == "finished"
    assert new_handle.output == "direct reply"
    assert parent.subagent_tracker.has_undelivered() is False  # uninterested -- never queued


def test_dispatch_direct_message_is_atomic_under_concurrent_calls_for_the_same_dormant_subagent(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two overlapping `dispatch_direct_message` calls for the same dormant subagent must not
    both see it as free to resume."""

    class _BlockingProvider(_FakeProvider):
        def __init__(self, release: threading.Event) -> None:
            super().__init__(reply_text="direct reply")
            self._release = release

        def send_prompt(self, *args: Any, **kwargs: Any) -> ProviderResponse:
            self._release.wait(timeout=5.0)
            return super().send_prompt(*args, **kwargs)

    release = threading.Event()
    provider = _BlockingProvider(release)
    process_config = ProcessConfig()
    parent = Session(make_session_config(role_name="operator", workspace=Workspace(path=tmp_path)),
                     provider=provider, process_config=process_config)
    child = Session(make_session_config(role_name="explorer"), provider=provider, parent=parent)
    dormant_handle = SubagentHandle(
        session=child, thread=threading.Thread(target=lambda: None), cancel_event=threading.Event(),
        role="explorer", title="task",
        outcome=SubagentTurnOutcome(output="earlier output", completed=True))
    parent.subagent_tracker.register(dormant_handle)

    dispatch_count = 0
    dispatch_count_lock = threading.Lock()
    real_dispatch_subagent_turn = dispatch_subagent_turn

    def counting_dispatch_subagent_turn(*args: Any, **kwargs: Any) -> SubagentHandle:
        nonlocal dispatch_count
        with dispatch_count_lock:
            dispatch_count += 1
        return real_dispatch_subagent_turn(*args, **kwargs)

    monkeypatch.setattr(
        "klorb.agents.policy.dispatch_subagent_turn", counting_dispatch_subagent_turn)

    barrier = threading.Barrier(2)
    results: list[Literal["queued", "started"]] = []
    results_lock = threading.Lock()

    def call_direct_message() -> None:
        barrier.wait(timeout=5.0)
        mode = dispatch_direct_message(process_config, child, dormant_handle, "poking in directly")
        with results_lock:
            results.append(mode)

    callers = [threading.Thread(target=call_direct_message) for _ in range(2)]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(timeout=5.0)
    release.set()

    assert sorted(results) == ["queued", "started"]
    assert dispatch_count == 1  # only the winner started a fresh turn
    new_handle = parent.subagent_tracker.handles()[0]
    new_handle.thread.join(timeout=5.0)
    assert new_handle.output is not None
    assert "direct reply" in new_handle.output


def test_subagent_finish_and_a_concurrent_enqueue_cannot_strand_the_message(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """A message enqueued while the worker is deciding whether to finish is drained into one
    more turn rather than sitting queued forever on a finished subagent. Holding the dispatch
    guard while enqueueing forces the worker's own guard-held finish decision to observe it."""
    provider = _FakeProvider(reply_text="reply")
    process_config = ProcessConfig()
    parent = Session(make_session_config(role_name="operator", workspace=Workspace(path=tmp_path)),
                     provider=provider, process_config=process_config)
    child = Session(make_session_config(role_name="explorer"), provider=provider, parent=parent)

    with parent.subagent_tracker.dispatch_guard():
        handle = dispatch_subagent_turn(process_config, parent, child, "explorer", "task", "go")
        deadline = time.monotonic() + 5.0
        while not provider.calls and time.monotonic() < deadline:
            time.sleep(0.01)
        child.enqueue_queued_message(QueuedMessage(message_text="one more thing"))

    handle.thread.join(timeout=5.0)
    current = parent.subagent_tracker.current_handle(child.id)
    assert current is not None
    assert current.state == "finished"
    assert child.pending_queued_message_texts == []
    assert any(
        m.role == "user" and "one more thing" in m.content for m in child.messages)
    assert len(provider.calls) == 2


def test_dispatch_direct_message_raises_transient_when_resuming_would_exceed_the_concurrent_limit(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    provider = _FakeProvider()
    process_config = ProcessConfig(subagents_max_concurrent_per_parent=1)
    parent = Session(make_session_config(role_name="operator", workspace=Workspace(path=tmp_path)),
                     provider=provider, process_config=process_config)
    never_finishes = threading.Event()
    running_child = Session(make_session_config(role_name="explorer"), provider=provider, parent=parent)
    running_handle = SubagentHandle(
        session=running_child, thread=threading.Thread(target=never_finishes.wait, daemon=True),
        cancel_event=threading.Event(), role="explorer", title="first")
    parent.subagent_tracker.register(running_handle)
    running_handle.thread.start()
    dormant_child = Session(make_session_config(role_name="explorer"), provider=provider, parent=parent)
    dormant_handle = SubagentHandle(
        session=dormant_child, thread=threading.Thread(target=lambda: None),
        cancel_event=threading.Event(), role="explorer", title="second",
        outcome=SubagentTurnOutcome(output="done", completed=True))
    parent.subagent_tracker.register(dormant_handle)

    try:
        with pytest.raises(ToolCallError, match="Call WaitForSubagent") as exc_info:
            dispatch_direct_message(process_config, dormant_child, dormant_handle, "hi")
        assert exc_info.value.category == "transient"
        # Rejected before anything was dispatched -- the dormant handle is untouched.
        assert parent.subagent_tracker.handles() == [running_handle, dormant_handle]
    finally:
        never_finishes.set()
        running_handle.thread.join(timeout=5.0)


def test_deliver_event_message_starts_a_fresh_turn_on_a_dormant_subagent(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """An event handler's message delivered to a dormant subagent (no turn running, no wake
    handler -- its ordinary between-turns state) starts a fresh, uninterested turn directly,
    mirroring `dispatch_direct_message`'s own dormant-child branch for a human message."""
    provider = _FakeProvider(reply_text="event reply")
    process_config = ProcessConfig()
    parent = Session(make_session_config(role_name="operator", workspace=Workspace(path=tmp_path)),
                     provider=provider, process_config=process_config)
    child = Session(
        make_session_config(role_name="explorer"), provider=provider, process_config=process_config,
        parent=parent)
    dormant_handle = SubagentHandle(
        session=child, thread=threading.Thread(target=lambda: None), cancel_event=threading.Event(),
        role="explorer", title="task",
        outcome=SubagentTurnOutcome(output="earlier output", completed=True))
    parent.subagent_tracker.register(dormant_handle)

    child.deliver_event_message("filesystem changed")

    new_handle = parent.subagent_tracker.handles()[0]
    new_handle.thread.join(timeout=5.0)
    assert new_handle is not dormant_handle
    assert new_handle.parent_interested is False
    assert new_handle.output == "event reply"


def test_deliver_event_message_skips_a_dormant_subagent_when_concurrency_limits_exceeded(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """No exception, no delivery -- just silently skipped, per docs/specs/hooks-and-events.md's
    "if this would cause too many subagents in parallel to wake up ... it is not delivered"."""
    provider = _FakeProvider(reply_text="event reply")
    process_config = ProcessConfig(subagents_max_concurrent_per_parent=0)
    parent = Session(make_session_config(role_name="operator", workspace=Workspace(path=tmp_path)),
                     provider=provider, process_config=process_config)
    child = Session(
        make_session_config(role_name="explorer"), provider=provider, process_config=process_config,
        parent=parent)
    dormant_handle = SubagentHandle(
        session=child, thread=threading.Thread(target=lambda: None), cancel_event=threading.Event(),
        role="explorer", title="task",
        outcome=SubagentTurnOutcome(output="earlier output", completed=True))
    parent.subagent_tracker.register(dormant_handle)

    child.deliver_event_message("filesystem changed")  # must not raise

    assert parent.subagent_tracker.handles() == [dormant_handle]  # no new turn was dispatched
    assert provider.calls == []


def _subagent_session_pair(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig], provider: _FakeProvider,
    hooks: dict[str, list[HookConfig]],
) -> tuple[ProcessConfig, Session, Session]:
    """`onSubagentStart`/`onSubagentTurnEnd` fire from the *parent*'s own `config.hooks`, so
    `hooks` goes on `parent`'s `SessionConfig`, not a shared `ProcessConfig`."""
    process_config = ProcessConfig()
    parent = Session(
        make_session_config(
            role_name="operator", workspace=Workspace(path=tmp_path, trusted=True), hooks=hooks),
        provider=provider, process_config=process_config)
    child = Session(
        make_session_config(role_name="explorer", workspace=Workspace(path=tmp_path, trusted=True)),
        provider=provider, process_config=process_config, parent=parent)
    return process_config, parent, child


def test_dispatch_subagent_turn_fires_onsubagentstart_and_rewrites_the_message(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider(reply_text="subagent reply")
    process_config, parent, child = _subagent_session_pair(tmp_path, make_session_config, provider, {
        "onSubagentStart": [HookConfig(type="bash", shell='echo \'{"message": "rewritten task"}\'')],
    })

    handle = dispatch_subagent_turn(process_config, parent, child, "explorer", "title", "original task")
    handle.thread.join(timeout=5.0)

    assert provider.calls
    user_message = next(m for m in provider.calls[0] if m.role == "user")
    assert user_message.content.endswith("rewritten task")


def test_onsubagentstart_hook_on_the_child_alone_does_not_fire(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """`onSubagentStart` is the parent's own observation of its child starting -- a handler
    configured only on the child's own `config.hooks` (never the parent's) must not fire."""
    provider = _FakeProvider(reply_text="subagent reply")
    process_config = ProcessConfig()
    parent = Session(
        make_session_config(role_name="operator", workspace=Workspace(path=tmp_path, trusted=True)),
        provider=provider, process_config=process_config)
    child = Session(
        make_session_config(
            role_name="explorer", workspace=Workspace(path=tmp_path, trusted=True),
            hooks={"onSubagentStart": [
                HookConfig(type="bash", shell='echo \'{"message": "rewritten task"}\'')]}),
        provider=provider, process_config=process_config, parent=parent)

    handle = dispatch_subagent_turn(process_config, parent, child, "explorer", "title", "original task")
    handle.thread.join(timeout=5.0)

    assert provider.calls
    user_message = next(m for m in provider.calls[0] if m.role == "user")
    assert user_message.content.endswith("original task")


def test_onsubagentstart_veto_blocks_the_turn_without_calling_the_model(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider()
    process_config, parent, child = _subagent_session_pair(tmp_path, make_session_config, provider, {
        "onSubagentStart": [HookConfig(type="bash", shell='echo \'{"success": false}\'')],
    })

    handle = dispatch_subagent_turn(process_config, parent, child, "explorer", "title", "original task")
    handle.thread.join(timeout=5.0)

    assert provider.calls == []
    assert handle.output == "(Subagent blocked by onSubagentStart hook policy.)"


def test_dispatch_subagent_turn_fires_onsubagentturnend_after_the_turn_ends(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    marker = tmp_path / "marker"
    provider = _FakeProvider(reply_text="subagent reply")
    process_config, parent, child = _subagent_session_pair(tmp_path, make_session_config, provider, {
        "onSubagentTurnEnd": [HookConfig(type="bash", shell=f'touch "{marker}"; echo \'{{}}\'')],
    })

    handle = dispatch_subagent_turn(process_config, parent, child, "explorer", "title", "task")
    handle.thread.join(timeout=5.0)

    assert marker.exists()


def test_dispatch_subagent_turn_chains_via_onsubagentturnend_until_the_cap_trips(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """A `chat` handler's `onSubagentTurnEnd` continuation is delivered via
    `Session._deliver_chained_hook_message`."""
    provider = _FakeProvider(reply_text="subagent reply")
    process_config, parent, child = _subagent_session_pair(tmp_path, make_session_config, provider, {
        "onSubagentTurnEnd": [HookConfig(type="chat", prompt="keep going")],
    })

    handle = dispatch_subagent_turn(process_config, parent, child, "explorer", "title", "task")
    handle.thread.join(timeout=5.0)

    # 1 original turn + child.config.max_chained_hook_turns (default 5) chained ones.
    assert len(provider.calls) == 6
    user_messages = [m.content for m in child.messages if m.role == "user"]
    assert user_messages[0].endswith("task")
    assert user_messages[1:] == ["keep going"] * 5


def test_dispatch_subagent_turn_relays_output_to_an_idle_root_parent(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """A finished, parent-interested subagent's output reaches an idle root parent right away, the
    same way SendMessage would deliver to it -- no explicit WaitForSubagent call needed."""
    provider = _FakeProvider(reply_text="subagent reply")
    process_config, parent, child = _subagent_session_pair(tmp_path, make_session_config, provider, {})
    woken = threading.Event()
    parent.register_wake_handler(woken.set)

    handle = dispatch_subagent_turn(process_config, parent, child, "explorer", "title", "task")
    handle.thread.join(timeout=5.0)

    assert woken.wait(timeout=5.0)
    assert handle.delivered is True
    queued_texts = parent.pending_queued_message_texts
    assert len(queued_texts) == 1
    assert "subagent reply" in queued_texts[0]
    parent_context = ToolSetupContext(
        process_config=process_config, session_config=parent.config, session=parent)
    with pytest.raises(ToolCallError, match="no subagents"):
        WaitForSubagentTool(parent_context).apply({})


def test_dispatch_subagent_turn_relay_exception_does_not_strand_the_handle(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """An unexpected exception out of the relay's delivery attempt must not permanently hide the
    handle from `WaitForSubagent`, and must not stop `dispatch_subagent_turn`'s worker from still
    calling `try_wake_next_queued_agent` afterward."""
    provider = _FakeProvider(reply_text="subagent reply")
    process_config, parent, child = _subagent_session_pair(tmp_path, make_session_config, provider, {})
    parent.register_wake_handler(lambda: None)
    parent.deliver_event_message = MagicMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    handle = dispatch_subagent_turn(process_config, parent, child, "explorer", "title", "task")
    handle.thread.join(timeout=5.0)

    assert handle.delivered is False
    parent_context = ToolSetupContext(
        process_config=process_config, session_config=parent.config, session=parent)
    result = WaitForSubagentTool(parent_context).apply({})
    assert result["completed"][0]["output"] == "subagent reply"


def test_dispatch_subagent_turn_does_not_relay_to_a_busy_parent(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """A parent with a turn already in flight when its child finishes must not get a
    self-addressed AgentMessageQueue entry -- that would spuriously interrupt a WaitForSubagent
    call already polling on that same turn with a "new message" that's actually just the
    completion it's about to return anyway. `mark_finished`'s completion-queue push already
    covers delivery for this case."""
    provider = _FakeProvider(reply_text="subagent reply")
    process_config, parent, child = _subagent_session_pair(tmp_path, make_session_config, provider, {})
    parent._current_turn_handlers = TurnEventHandlers()
    try:
        handle = dispatch_subagent_turn(process_config, parent, child, "explorer", "title", "task")
        handle.thread.join(timeout=5.0)

        assert handle.delivered is False
        assert parent.subagent_tracker.has_undelivered()
        assert not get_agent_message_queue(parent).has_pending(parent.id)
        assert parent.pending_queued_message_texts == []
    finally:
        parent._current_turn_handlers = None


def test_dispatch_subagent_turn_wakes_a_dormant_subagent_parent(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """A finished child's output actively wakes its own dormant subagent parent -- not just the
    parent's parent -- fixing what would otherwise be a silent drop under the old
    `_deliver_event_to_subagent`-style approach."""
    provider = _FakeProvider(reply_text="child reply")
    process_config = ProcessConfig()
    grandparent = Session(
        make_session_config(role_name="operator", workspace=Workspace(path=tmp_path, trusted=True)),
        provider=provider, process_config=process_config)
    parent = Session(
        make_session_config(role_name="operator", workspace=Workspace(path=tmp_path, trusted=True)),
        provider=provider, process_config=process_config, parent=grandparent)
    dormant_parent_handle = SubagentHandle(
        session=parent, thread=threading.Thread(target=lambda: None), cancel_event=threading.Event(),
        role="operator", title="parent task",
        outcome=SubagentTurnOutcome(output="earlier parent output", completed=True))
    grandparent.subagent_tracker.register(dormant_parent_handle)
    child = Session(
        make_session_config(role_name="explorer", workspace=Workspace(path=tmp_path, trusted=True)),
        provider=provider, process_config=process_config, parent=parent)

    handle = dispatch_subagent_turn(process_config, parent, child, "explorer", "title", "task")
    handle.thread.join(timeout=5.0)

    assert handle.delivered is True
    woken_handle = grandparent.subagent_tracker.handles()[-1]
    assert woken_handle is not dormant_parent_handle
    woken_handle.thread.join(timeout=5.0)
    assert len(provider.calls) >= 2
    relayed_prompt = next(m for m in provider.calls[1] if m.role == "user").content
    assert "child reply" in relayed_prompt
    assert f"From {child.id}" in relayed_prompt


def test_dispatch_subagent_turn_leaves_a_capacity_blocked_parent_queued_not_delivered(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """When waking the dormant parent would exceed `maxConcurrentPerParent`, the relay leaves the
    message queued instead of delivering it or dropping it -- discoverable later once capacity
    frees up, the same fairness guarantee `try_wake_next_queued_agent` gives any other queued
    agent message."""
    provider = _FakeProvider(reply_text="child reply")
    process_config = ProcessConfig(subagents_max_concurrent_per_parent=0)
    grandparent = Session(
        make_session_config(role_name="operator", workspace=Workspace(path=tmp_path, trusted=True)),
        provider=provider, process_config=process_config)
    parent = Session(
        make_session_config(role_name="operator", workspace=Workspace(path=tmp_path, trusted=True)),
        provider=provider, process_config=process_config, parent=grandparent)
    dormant_parent_handle = SubagentHandle(
        session=parent, thread=threading.Thread(target=lambda: None), cancel_event=threading.Event(),
        role="operator", title="parent task",
        outcome=SubagentTurnOutcome(output="earlier parent output", completed=True))
    grandparent.subagent_tracker.register(dormant_parent_handle)
    child = Session(
        make_session_config(role_name="explorer", workspace=Workspace(path=tmp_path, trusted=True)),
        provider=provider, process_config=process_config, parent=parent)

    handle = dispatch_subagent_turn(process_config, parent, child, "explorer", "title", "task")
    handle.thread.join(timeout=5.0)

    assert handle.delivered is False
    assert get_agent_message_queue(parent).has_pending(parent.id)
    assert grandparent.subagent_tracker.handles() == [dormant_parent_handle]


def test_dispatch_subagent_turn_does_not_relay_a_human_addressed_completion(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """`parent_interested=False` (a human addressed this subagent directly) must never surface to
    the parent through the relay either, exactly as it never did through `WaitForSubagent`."""
    provider = _FakeProvider(reply_text="subagent reply")
    process_config, parent, child = _subagent_session_pair(tmp_path, make_session_config, provider, {})
    woken = threading.Event()
    parent.register_wake_handler(woken.set)

    handle = dispatch_subagent_turn(
        process_config, parent, child, "explorer", "title", "task", parent_interested=False)
    handle.thread.join(timeout=5.0)

    assert not woken.is_set()
    assert parent.pending_queued_message_texts == []
    assert handle.delivered is False
