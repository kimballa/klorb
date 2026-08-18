# © Copyright 2026 Aaron Kimball
"""Tests for klorb.agents.policy: the CreateSubagent rejection checks and the tool/skill/role
intersection wiring that produces a subagent's SessionConfig and tool registry -- driven
against the real, packaged agents.json ("operator"/"explorer"/"reviewer"/"planner"/"implementer"
entries) rather than a
fixture, since these are exactly the entries CreateSubagent consults in production. See
docs/specs/subagents.md.
"""
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from unittest.mock import MagicMock

import pytest
from tools.subagents.conftest import _FakeProvider

from klorb.agents.policy import (
    compute_root_session_grants,
    dispatch_direct_message,
    dispatch_subagent_turn,
    plan_subagent_creation,
)
from klorb.agents.runtime import SUBAGENT_MGMT_TOOL_NAMES, SubagentHandle
from klorb.api_provider import ProviderResponse
from klorb.hooks.config import HookConfig
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.exceptions import ToolCallError
from klorb.tools.registry import ToolRegistry
from klorb.tools.setup_context import ToolSetupContext
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
    """A caller whose own `role_name` has no `agents.json` entry at all -- e.g. a typo, or a role
    retired from the file -- is rejected the same way a role with `allow_subagents: false` would
    be (both hit `caller_definition is None or not caller_definition.allow_subagents`); both
    packaged roles (operator, explorer) currently have `allow_subagents: true`, so an unknown role
    name is what exercises this branch against the real file."""
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
    """operator's own agents.json entry names `restrict_to.subagent_roles: ["explorer",
    "reviewer", "planner", "implementer"]`. `_operator_context` builds its root session via
    `compute_root_session_grants`, the same path every real root `Session` construction site
    uses, so `effective_subagent_roles` is already `{"explorer", "implementer", "planner",
    "reviewer"}` by the time `plan_subagent_creation` reads it -- not "every role agents.json
    defines" -- so a root operator session can't spawn another operator."""
    context = _operator_context(tmp_path, make_session_config)

    with pytest.raises(ToolCallError, match="not among the subagent roles") as exc_info:
        plan_subagent_creation(context, "operator", None, None)
    assert "['explorer', 'implementer', 'planner', 'reviewer']" in str(exc_info.value)


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
    """A subagent session is never destroyed once it finishes its turn -- it sits dormant,
    possibly still undelivered, until MessageSubagent resumes it. That dormant backlog must not
    itself block creating a new subagent under a tight maxConcurrentPerParent -- only a turn
    that's actually running should occupy a slot."""
    context = _operator_context(tmp_path, make_session_config, max_concurrent=1)
    assert context.session is not None
    child = Session(make_session_config(role_name="explorer"), provider=MagicMock(), parent=context.session)
    handle = SubagentHandle(
        session=child, thread=threading.Thread(target=lambda: None), cancel_event=threading.Event(),
        role="explorer", title="earlier task")
    context.session.subagent_tracker.register(handle)
    context.session.subagent_tracker.mark_finished(child.id, "done")

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
    assert plan.role_definition.default_model == "xiaomi/mimo-v2.5"


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
    """A human messaging a still-running subagent just enqueues into its current turn -- it does
    not start a second, competing one, and (per SubagentHandle's own docstring) never touches
    `parent_interested` on the handle that turn was originally dispatched under."""
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
        role="explorer", title="task", state="finished", output="earlier output")
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
        role="explorer", title="task", state="finished", output="earlier output")
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
    assert dispatch_count == 1  # only the winner started a fresh turn -- the loser just enqueued
    new_handle = parent.subagent_tracker.handles()[0]
    new_handle.thread.join(timeout=5.0)
    assert new_handle.output is not None
    assert "direct reply" in new_handle.output


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
        cancel_event=threading.Event(), role="explorer", title="second", state="finished",
        output="done")
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


def _subagent_session_pair(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig], provider: _FakeProvider,
    hooks: dict[str, list[HookConfig]],
) -> tuple[Session, Session]:
    process_config = ProcessConfig(hooks=hooks)
    parent = Session(
        make_session_config(role_name="operator", workspace=Workspace(path=tmp_path, trusted=True)),
        provider=provider, process_config=process_config)
    child = Session(
        make_session_config(role_name="explorer", workspace=Workspace(path=tmp_path, trusted=True)),
        provider=provider, process_config=process_config, parent=parent)
    return parent, child


def test_dispatch_subagent_turn_fires_onsubagentstart_and_rewrites_the_message(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider(reply_text="subagent reply")
    parent, child = _subagent_session_pair(tmp_path, make_session_config, provider, {
        "onSubagentStart": [HookConfig(type="bash", shell='echo \'{"message": "rewritten task"}\'')],
    })

    handle = dispatch_subagent_turn(parent, child, "explorer", "title", "original task")
    handle.thread.join(timeout=5.0)

    assert provider.calls
    user_message = next(m for m in provider.calls[0] if m.role == "user")
    assert user_message.content.endswith("rewritten task")


def test_onsubagentstart_veto_blocks_the_turn_without_calling_the_model(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = _FakeProvider()
    parent, child = _subagent_session_pair(tmp_path, make_session_config, provider, {
        "onSubagentStart": [HookConfig(type="bash", shell='echo \'{"success": false}\'')],
    })

    handle = dispatch_subagent_turn(parent, child, "explorer", "title", "original task")
    handle.thread.join(timeout=5.0)

    assert provider.calls == []
    assert handle.output == "(Subagent blocked by onSubagentStart hook policy.)"


def test_dispatch_subagent_turn_fires_onsubagentturnend_after_the_turn_ends(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    marker = tmp_path / "marker"
    provider = _FakeProvider(reply_text="subagent reply")
    parent, child = _subagent_session_pair(tmp_path, make_session_config, provider, {
        "onSubagentTurnEnd": [HookConfig(type="bash", shell=f'touch "{marker}"; echo \'{{}}\'')],
    })

    handle = dispatch_subagent_turn(parent, child, "explorer", "title", "task")
    handle.thread.join(timeout=5.0)

    assert marker.exists()


def test_dispatch_subagent_turn_chains_via_onsubagentturnend_until_the_cap_trips(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """A `chat` handler's `onSubagentTurnEnd` continuation is delivered via
    `Session._deliver_chained_hook_message`, exactly like the root session's own `onAgentTurnEnd`
    chaining -- `_run_subagent_turn`'s own loop (`klorb.agents.policy`) is the "host" that drains
    and resubmits it, since nothing else runs a subagent session's turns."""
    provider = _FakeProvider(reply_text="subagent reply")
    parent, child = _subagent_session_pair(tmp_path, make_session_config, provider, {
        "onSubagentTurnEnd": [HookConfig(type="chat", prompt="keep going")],
    })

    handle = dispatch_subagent_turn(parent, child, "explorer", "title", "task")
    handle.thread.join(timeout=5.0)

    # 1 original turn + child.config.max_chained_hook_turns (default 5) chained ones.
    assert len(provider.calls) == 6
    user_messages = [m.content for m in child.messages if m.role == "user"]
    assert user_messages[0].endswith("task")
    assert user_messages[1:] == ["keep going"] * 5
