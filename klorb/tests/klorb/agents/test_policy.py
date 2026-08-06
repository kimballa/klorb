# © Copyright 2026 Aaron Kimball
"""Tests for klorb.agents.policy: the CreateSubagent rejection checks and the tool/skill/role
intersection wiring that produces a subagent's SessionConfig and tool registry -- driven
against the real, packaged agents.json ("operator"/"explorer" entries) rather than a fixture,
since these are exactly the entries CreateSubagent consults in production. See
docs/specs/subagents.md.
"""

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from klorb.agents.policy import compute_root_session_grants, plan_subagent_creation
from klorb.agents.runtime import SUBAGENT_MGMT_TOOL_NAMES, SubagentHandle
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.exceptions import ToolCallError
from klorb.tools.registry import ToolRegistry
from klorb.tools.setup_context import ToolSetupContext
from klorb.workspace import Workspace


def _operator_context(
    tmp_path: Path, *, max_concurrent: int = 4, max_active: int = 16, max_depth: int = 2,
) -> ToolSetupContext:
    process_config = ProcessConfig(
        subagents_max_concurrent_per_parent=max_concurrent,
        subagents_max_active_total=max_active, subagents_max_depth=max_depth)
    session_config = SessionConfig(role_name="operator", workspace=Workspace(path=tmp_path))
    grants = compute_root_session_grants(process_config, session_config, session_config.role_name)
    session_config.skill_rules = grants.skill_rules
    session = Session(
        session_config, provider=MagicMock(), process_config=process_config,
        tool_registry=grants.tool_registry, effective_subagent_roles=grants.effective_subagent_roles)
    return ToolSetupContext(process_config=process_config, session_config=session_config, session=session)


def _no_child_roles_context(
    tmp_path: Path, *, max_concurrent: int = 4, max_active: int = 16, max_depth: int = 2,
) -> ToolSetupContext:
    process_config = ProcessConfig(
        subagents_max_concurrent_per_parent=max_concurrent,
        subagents_max_active_total=max_active, subagents_max_depth=max_depth)
    session_config = SessionConfig(role_name="operator", workspace=Workspace(path=tmp_path))
    grants = compute_root_session_grants(process_config, session_config, session_config.role_name)
    session_config.skill_rules = grants.skill_rules
    session = Session(
        session_config, provider=MagicMock(), process_config=process_config,
        tool_registry=grants.tool_registry, effective_subagent_roles=[])
    return ToolSetupContext(process_config=process_config, session_config=session_config, session=session)


def test_rejects_when_depth_would_exceed_max_depth(tmp_path: Path) -> None:
    context = _operator_context(tmp_path, max_depth=1)
    assert context.session is not None
    context.session.depth = 1  # simulate this session already being one hop below the root

    with pytest.raises(ToolCallError) as exc_info:
        plan_subagent_creation(context, "explorer", None, None)
    assert exc_info.value.category == "validation"
    assert "depth" in str(exc_info.value)


def test_rejects_when_callers_own_role_disallows_subagents(tmp_path: Path) -> None:
    """A caller whose own `role_name` has no `agents.json` entry at all -- e.g. a typo, or a role
    retired from the file -- is rejected the same way a role with `allow_subagents: false` would
    be (both hit `caller_definition is None or not caller_definition.allow_subagents`); both
    packaged roles (operator, explorer) currently have `allow_subagents: true`, so an unknown role
    name is what exercises this branch against the real file."""
    process_config = ProcessConfig()
    session_config = SessionConfig(role_name="no_such_role", workspace=Workspace(path=tmp_path))
    tool_registry = ToolRegistry.discover_tools(process_config, session_config)
    session = Session(
        session_config, provider=MagicMock(), process_config=process_config, tool_registry=tool_registry)
    context = ToolSetupContext(process_config=process_config,
                               session_config=session_config, session=session)

    with pytest.raises(ToolCallError, match="may not create subagents"):
        plan_subagent_creation(context, "explorer", None, None)


def test_rejects_unknown_role(tmp_path: Path) -> None:
    context = _operator_context(tmp_path)

    with pytest.raises(ToolCallError, match="not among the subagent roles") as exc_info:
        plan_subagent_creation(context, "no_such_role", None, None)
    # The error names the roles the agent may actually retry with.
    assert "explorer" in str(exc_info.value)


def test_operator_cannot_launch_another_operator(tmp_path: Path) -> None:
    """operator's own agents.json entry names `restrict_to.subagent_roles: ["explorer"]`.
    `_operator_context` builds its root session via `compute_root_session_grants`, the same path
    every real root `Session` construction site uses, so `effective_subagent_roles` is already
    `{"explorer"}` by the time `plan_subagent_creation` reads it -- not "every role agents.json
    defines" -- so a root operator session can't spawn another operator."""
    context = _operator_context(tmp_path)

    with pytest.raises(ToolCallError, match="not among the subagent roles") as exc_info:
        plan_subagent_creation(context, "operator", None, None)
    assert "['explorer']" in str(exc_info.value)


def test_informed_cannot_launch_subagents_when_empty_roles_list(tmp_path: Path) -> None:
    """When the agent's allowed subagent roles list is the empty list, it is explicitly
    told in the CreateSubagent error message that it may not create subagents.
    """
    context = _no_child_roles_context(tmp_path)

    with pytest.raises(ToolCallError, match="may not create subagents") as exc_info:
        plan_subagent_creation(context, "explorer", None, None)
    assert "may not create subagents" in str(exc_info.value)


def test_rejects_role_outside_the_callers_own_effective_subagent_roles(tmp_path: Path) -> None:
    process_config = ProcessConfig()
    session_config = SessionConfig(role_name="operator", workspace=Workspace(path=tmp_path))
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


def test_rejects_when_concurrent_per_parent_limit_already_reached(tmp_path: Path) -> None:
    context = _operator_context(tmp_path, max_concurrent=0)

    with pytest.raises(ToolCallError, match="Call WaitForSubagent") as exc_info:
        plan_subagent_creation(context, "explorer", None, None)
    assert exc_info.value.category == "transient"


def test_rejects_when_active_total_limit_already_reached(tmp_path: Path) -> None:
    context = _operator_context(tmp_path, max_active=0)

    with pytest.raises(ToolCallError, match="Call WaitForSubagent") as exc_info:
        plan_subagent_creation(context, "explorer", None, None)
    assert exc_info.value.category == "transient"


def test_finished_but_undelivered_subagent_does_not_count_toward_concurrent_limit(
    tmp_path: Path,
) -> None:
    """A subagent session is never destroyed once it finishes its turn -- it sits dormant,
    possibly still undelivered, until MessageSubagent resumes it. That dormant backlog must not
    itself block creating a new subagent under a tight maxConcurrentPerParent -- only a turn
    that's actually running should occupy a slot."""
    context = _operator_context(tmp_path, max_concurrent=1)
    assert context.session is not None
    child = Session(SessionConfig(role_name="explorer"), provider=MagicMock(), parent=context.session)
    handle = SubagentHandle(
        session=child, thread=threading.Thread(target=lambda: None), cancel_event=threading.Event(),
        role="explorer", title="earlier task")
    context.session.subagent_tracker.register(handle)
    context.session.subagent_tracker.mark_finished(child.id, "done")

    plan = plan_subagent_creation(context, "explorer", None, None)  # must not raise

    assert plan.session_config.role_name == "explorer"


def test_no_session_is_constructed_when_a_check_fails(tmp_path: Path) -> None:
    context = _operator_context(tmp_path, max_depth=0)
    assert context.session is not None
    context.session.depth = 0

    with pytest.raises(ToolCallError):
        plan_subagent_creation(context, "explorer", None, None)

    assert context.session.subagent_tracker.handles() == []


def test_explorer_plan_excludes_subagent_management_tools(tmp_path: Path) -> None:
    context = _operator_context(tmp_path)

    plan = plan_subagent_creation(context, "explorer", None, None)

    assert not (set(plan.tool_classes) & SUBAGENT_MGMT_TOOL_NAMES)
    assert "ReadFile" in plan.tool_classes
    assert "Grep" in plan.tool_classes
    # EditFile isn't in the explorer role's restrict_to.tools list at all.
    assert "EditFile" not in plan.tool_classes


def test_explorer_plan_session_config_carries_the_explorer_role(tmp_path: Path) -> None:
    context = _operator_context(tmp_path)

    plan = plan_subagent_creation(context, "explorer", None, None)

    assert plan.session_config.role_name == "explorer"
    assert plan.role_definition.default_model == "xiaomi/mimo-v2.5"


def test_allowed_tools_override_replaces_the_roles_own_list(tmp_path: Path) -> None:
    context = _operator_context(tmp_path)

    plan = plan_subagent_creation(context, "explorer", ["FindFile", "SearchSkills"], None)

    # "SearchSkills" isn't in explorer's own restrict_to.tools, but the override still can't
    # grant anything the *parent* (operator, here with the full real tool catalog) doesn't have
    # -- since the parent does have it, and it's read-only, this override is honored despite
    # exceeding the role's own nominal list.
    assert set(plan.tool_classes) == {"FindFile", "SearchSkills"}


def test_allowed_tools_override_still_respects_enforce_readonly_tools(tmp_path: Path) -> None:
    context = _operator_context(tmp_path)

    # "Bash" is not read-only; explorer's own enforce_readonly_tools=True isn't touched by the
    # allowed_tools override (only the "tools" field is), so it's still clamped out.
    plan = plan_subagent_creation(context, "explorer", ["FindFile", "Bash"], None)

    assert set(plan.tool_classes) == {"FindFile"}


def test_allowed_tools_override_still_cannot_exceed_the_parents_own_tool_set(tmp_path: Path) -> None:
    context = _operator_context(tmp_path)

    plan = plan_subagent_creation(context, "explorer", ["FindFile", "NoSuchTool"], None)

    assert set(plan.tool_classes) == {"FindFile"}


def test_subagent_roles_are_the_explorer_roles_own_restriction_intersected_with_the_parents(
    tmp_path: Path,
) -> None:
    context = _operator_context(tmp_path)

    plan = plan_subagent_creation(context, "explorer", None, None)

    # explorer's restrict_to.subagent_roles names ["explorer", "vision_assistant"], but
    # "vision_assistant" has no agents.json entry yet (added in a later phase) -- it can't
    # survive the intersection against the operator parent's own effective roles (its own
    # restrict_to.subagent_roles, ["explorer"] -- operator may launch an explorer but not
    # another operator).
    assert plan.effective_subagent_roles == {"explorer"}
