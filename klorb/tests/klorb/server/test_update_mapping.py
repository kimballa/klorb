# © Copyright 2026 Aaron Kimball
"""Tests for `klorb.server.update_mapping` -- pure klorb-event-to-ACP-tool-call-update mapping.
See docs/specs/klorb-server.md's tool-call update mapping section."""
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import acp
import pytest
from acp.schema import AllowedOutcome, DeniedOutcome
from fixtures.sample_models import sample_model_registry

from klorb.message import Message, MessageFragment, MessageRole, ToolCallRequest
from klorb.permissions.command_grant import compute_command_grant_patterns
from klorb.permissions.directory_access import DirRules, canonicalize_dir
from klorb.permissions.resource import BashCommandContext, CommandResource, PathResource, StructuralResource
from klorb.permissions.risk_classifier import ItemRiskAssessment
from klorb.process_config import ProcessConfig
from klorb.server.update_mapping import (
    SESSION_MODES,
    TOOL_KIND_MAP,
    TOOL_LOCATION_ARG,
    _diff_text,
    ask_user_questions_ext_params,
    build_session_replay,
    escalate_privileges_decision_from_outcome,
    escalate_privileges_meta,
    escalate_privileges_options,
    parse_session_config_update,
    permission_ask_meta,
    permission_ask_options,
    permission_ask_tool_call_update,
    permission_decision_from_outcome,
    permission_decision_grant_patterns,
    session_config_json,
    session_mode_state,
    tool_call_finished_update,
    tool_call_started_update,
)
from klorb.session import Session, SessionConfig, WorkspaceAccess
from klorb.session.events import (
    AskUserQuestionsItemContext,
    EscalatePrivilegesContext,
    PermissionAskContext,
    ToolCallEvent,
    ToolCallStartedEvent,
)
from klorb.tools.ask.common import QuestionOption
from klorb.tools.registry import ToolRegistry
from klorb.tools.util import build_diff_hunks
from klorb.workspace import Workspace

# A future tool this dict genuinely can't classify yet would show up here, with a comment
# explaining why -- empty today, since every production tool has an explicit TOOL_KIND_MAP entry.
_UNMAPPED_OK: frozenset[str] = frozenset()


def _registry(tmp_path: Path) -> ToolRegistry:
    config = SessionConfig(
        model="some/model",
        workspace_access=WorkspaceAccess(
            workspace=Workspace(path=tmp_path, trusted=True),
            read_dirs=DirRules(allow=[tmp_path]),
            write_dirs=DirRules(allow=[tmp_path]),
        ),
    )
    return ToolRegistry.discover_tools(ProcessConfig(), config)


def test_kind_map_covers_every_registered_tool_name(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    unmapped = [
        tool.name() for tool in registry.tools()
        if tool.name() not in TOOL_KIND_MAP and tool.name() not in _UNMAPPED_OK
    ]
    assert unmapped == [], (
        f"Tool(s) {unmapped} have no TOOL_KIND_MAP entry and aren't in _UNMAPPED_OK -- add one "
        "of the two so a future tool doesn't silently render as ToolKind 'other'.")


def test_unknown_tool_name_falls_back_to_other_kind(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    event = ToolCallStartedEvent(call_id="1", name="NotARealTool", args={})

    update = tool_call_started_update(event, registry, tmp_path)

    assert update.kind == "other"
    assert update.title == "NotARealTool"


def test_started_update_title_uses_tool_summary(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    event = ToolCallStartedEvent(call_id="1", name="ListDir", args={"dirname": ""})

    update = tool_call_started_update(event, registry, tmp_path)

    assert update.kind == "search"
    assert update.status == "in_progress"
    assert update.tool_call_id == "1"
    assert "List dir" in update.title


def test_started_update_carries_tool_name_meta(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    event = ToolCallStartedEvent(call_id="1", name="CreateSubagent", args={})

    update = tool_call_started_update(event, registry, tmp_path)

    assert update.kind == "other"
    assert update.field_meta is not None
    assert update.field_meta["klorb"]["toolName"] == "CreateSubagent"


def test_read_file_location_resolves_relative_path_and_sets_line(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    event = ToolCallStartedEvent(
        call_id="1", name="ReadFile", args={"filename": "sub/foo.txt", "start_line": 5})

    update = tool_call_started_update(event, registry, tmp_path)

    expected_path = str(canonicalize_dir(Path("sub/foo.txt"), tmp_path))
    assert update.locations is not None
    assert len(update.locations) == 1
    assert update.locations[0].path == expected_path
    assert update.locations[0].line == 5


def test_tool_with_no_location_arg_emits_no_locations(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    event = ToolCallStartedEvent(call_id="1", name="Bash", args={"command": "echo hi"})

    update = tool_call_started_update(event, registry, tmp_path)

    assert update.locations is None


def test_location_arg_present_in_map_but_missing_from_call_emits_no_locations(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    event = ToolCallStartedEvent(call_id="1", name="Grep", args={"pattern": "foo"})

    update = tool_call_started_update(event, registry, tmp_path)

    assert update.locations is None


@pytest.mark.parametrize("name", sorted(TOOL_LOCATION_ARG))
def test_every_location_arg_entry_matches_the_tools_real_parameter(name: str, tmp_path: Path) -> None:
    """Guards against `TOOL_LOCATION_ARG` drifting from a tool's actual argument name (a real
    risk here: several of these tools name their path argument `filename`/`dirname`, not
    `path`)."""
    registry = _registry(tmp_path)
    tool = registry.instantiate_tool(name)
    arg_key = TOOL_LOCATION_ARG[name]
    parameters = tool.parameters()

    assert isinstance(parameters, dict)
    assert arg_key in parameters["properties"]


def test_diff_text_reassembles_add_only_hunk_with_no_old_text() -> None:
    hunks = build_diff_hunks([], ["a", "b"])

    old_text, new_text = _diff_text(hunks)

    assert old_text is None
    assert new_text == "a\nb"


def test_diff_text_reassembles_del_only_hunk() -> None:
    hunks = build_diff_hunks(["a", "b"], [])

    old_text, new_text = _diff_text(hunks)

    assert old_text == "a\nb"
    assert new_text == ""


def test_diff_text_reassembles_mixed_hunk_with_context() -> None:
    hunks = build_diff_hunks(["a", "b", "c"], ["a", "B", "c"], context=1)

    old_text, new_text = _diff_text(hunks)

    assert old_text == "a\nb\nc"
    assert new_text == "a\nB\nc"


def test_diff_text_reassembles_multiple_separated_hunks() -> None:
    old_lines = [str(i) for i in range(30)]
    new_lines = list(old_lines)
    new_lines[2] = "X"
    new_lines[27] = "Y"

    hunks = build_diff_hunks(old_lines, new_lines, context=2)

    assert len(hunks) == 2
    old_text, new_text = _diff_text(hunks)
    assert old_text is not None
    assert "X" in new_text
    assert "Y" in new_text
    assert "2" in old_text.split("\n")


def test_finished_update_emits_diff_content_for_a_successful_edit_file_call(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    (tmp_path / "foo.txt").write_text("a\nb\nc\n")
    tool = registry.instantiate_tool("EditFile")
    args = {"filename": "foo.txt", "old_text": "b", "new_text": "B"}
    result = tool.apply(args)
    event = ToolCallEvent(call_id="1", name="EditFile", args=args, result=result, error=None)

    update = tool_call_finished_update(event, registry, tmp_path)

    assert update.status == "completed"
    assert update.content is not None
    assert len(update.content) == 1
    block = update.content[0]
    assert block.type == "diff"
    assert block.path == str(canonicalize_dir(Path("foo.txt"), tmp_path))
    assert "B" in block.new_text
    assert block.field_meta is not None
    assert block.field_meta["klorb"]["diffHunks"]


def test_finished_update_falls_back_to_detail_view_for_a_non_diff_tool(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    (tmp_path / "foo.txt").write_text("a\nb\nc\n")
    tool = registry.instantiate_tool("ReadFile")
    args = {"filename": "foo.txt"}
    result = tool.apply(args)
    event = ToolCallEvent(call_id="1", name="ReadFile", args=args, result=result, error=None)

    update = tool_call_finished_update(event, registry, tmp_path)

    assert update.status == "completed"
    assert update.content is not None
    assert len(update.content) == 1
    block = update.content[0]
    assert block.type == "content"
    assert "foo.txt" in block.content.text


def test_finished_update_bash_meta_carries_command_alongside_result_fields(tmp_path: Path) -> None:
    """`command` must survive onto the finish update's `_meta.klorb.bash`, not just the start
    update's -- the webview client (`parseBashMeta`) treats a missing `command` string as an
    absent payload and discards `success`/`exitStatus`/`runtime` along with it."""
    registry = _registry(tmp_path)
    args = {"command": "echo hi", "intent": "say hi"}
    result = {"success": True, "exit_status": 0, "runtime": 1.234, "stdout": "hi\n"}
    event = ToolCallEvent(call_id="1", name="Bash", args=args, result=result, error=None)

    update = tool_call_finished_update(event, registry, tmp_path)

    assert update.field_meta is not None
    bash_meta = update.field_meta["klorb"]["bash"]
    assert bash_meta["command"] == "echo hi"
    assert bash_meta["intent"] == "say hi"
    assert bash_meta["success"] is True
    assert bash_meta["exitStatus"] == 0
    assert bash_meta["runtime"] == 1.23
    assert bash_meta["stdout"] == "hi\n"


def test_finished_update_bash_meta_omits_stdout_stderr_when_spilled(tmp_path: Path) -> None:
    """When stdout/stderr are `None` (spilled to file), they should not appear in bash meta."""
    registry = _registry(tmp_path)
    args = {"command": "cat bigfile", "intent": "read big"}
    result = {"success": True, "exit_status": 0, "runtime": 0.5,
              "stdout": None, "stderr": None, "stdout_file": "/tmp/x", "stderr_file": "/tmp/y"}
    event = ToolCallEvent(call_id="1", name="Bash", args=args, result=result, error=None)

    update = tool_call_finished_update(event, registry, tmp_path)

    assert update.field_meta is not None
    bash_meta = update.field_meta["klorb"]["bash"]
    assert "stdout" not in bash_meta
    assert "stderr" not in bash_meta


def test_finished_update_readfile_meta_carries_content_and_filename(tmp_path: Path) -> None:
    """A finished ReadFile call should include `_meta.klorb.readFile` with truncated content."""
    registry = _registry(tmp_path)
    (tmp_path / "hello.txt").write_text("a\nb\nc\n")
    tool = registry.instantiate_tool("ReadFile")
    args = {"filename": "hello.txt"}
    result = tool.apply(args)
    event = ToolCallEvent(call_id="1", name="ReadFile", args=args, result=result, error=None)

    update = tool_call_finished_update(event, registry, tmp_path)

    assert update.field_meta is not None
    readfile_meta = update.field_meta["klorb"]["readFile"]
    assert readfile_meta["filename"] == "hello.txt"
    assert "a" in readfile_meta["content"]
    assert "b" in readfile_meta["content"]
    assert "c" in readfile_meta["content"]


def test_finished_update_readfile_meta_for_read_memory(tmp_path: Path) -> None:
    """A finished ReadMemory call should also include `_meta.klorb.readFile`."""
    registry = _registry(tmp_path)
    args = {"namespace": "workspace", "filename": "notes.md"}
    result = {
        "content": "1|a\n2|b", "namespace": "workspace", "filename": "notes.md",
        "start_line": 1, "end_line": 2, "total_lines": 2, "truncated": False,
    }
    event = ToolCallEvent(call_id="1", name="ReadMemory", args=args, result=result, error=None)

    update = tool_call_finished_update(event, registry, tmp_path)

    assert update.field_meta is not None
    readfile_meta = update.field_meta["klorb"]["readFile"]
    assert readfile_meta["filename"] == "notes.md"
    assert "a" in readfile_meta["content"]
    assert "b" in readfile_meta["content"]


def test_finished_update_readfile_meta_for_read_scratchpad_falls_back_to_tool_name(
    tmp_path: Path,
) -> None:
    """`ReadScratchpad` has no `filename` arg or result field, so `readFile.filename` falls
    back to the tool's own name."""
    registry = _registry(tmp_path)
    result = {
        "content": "1|note", "start_line": 1, "end_line": 1, "total_lines": 1, "truncated": False,
    }
    event = ToolCallEvent(call_id="1", name="ReadScratchpad", args={}, result=result, error=None)

    update = tool_call_finished_update(event, registry, tmp_path)

    assert update.field_meta is not None
    readfile_meta = update.field_meta["klorb"]["readFile"]
    assert readfile_meta["filename"] == "ReadScratchpad"
    assert "note" in readfile_meta["content"]


def test_finished_update_reports_failure_text_and_status(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    event = ToolCallEvent(
        call_id="1", name="ReadFile", args={"filename": "missing.txt"},
        result=None, error="No such file: missing.txt")

    update = tool_call_finished_update(event, registry, tmp_path)

    assert update.status == "failed"
    assert update.content is not None
    block = update.content[0]
    assert block.content.text == "No such file: missing.txt"


def test_finished_update_includes_raw_arguments_for_malformed_json_call(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    event = ToolCallEvent(
        call_id="1", name="EditFile", args={}, result=None,
        error="Invalid JSON in tool call arguments for 'EditFile'.",
        raw_arguments='{"filename": "x.py", "new_text": "bad}')

    update = tool_call_finished_update(event, registry, tmp_path)

    assert update.status == "failed"
    assert update.content is not None
    text = update.content[0].content.text
    assert "Invalid JSON" in text
    assert '{"filename": "x.py", "new_text": "bad}' in text


def test_finished_update_omits_raw_output_when_not_json_serializable(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    event = ToolCallEvent(
        call_id="1", name="ListDir", args={"dirname": ""}, result=object(), error=None)

    update = tool_call_finished_update(event, registry, tmp_path)

    assert update.raw_output is None


def test_finished_update_keeps_json_serializable_raw_output(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    event = ToolCallEvent(
        call_id="1", name="ListDir", args={"dirname": ""},
        result={"subdirs": [], "files": ["a.txt"], "child_count": 1}, error=None)

    update = tool_call_finished_update(event, registry, tmp_path)

    assert update.raw_output == {"subdirs": [], "files": ["a.txt"], "child_count": 1}


def test_finished_update_handles_no_tool_registry_at_all(tmp_path: Path) -> None:
    event = ToolCallEvent(call_id="1", name="ReadFile", args={
                          "filename": "foo.txt"}, result="ok", error=None)

    update = tool_call_finished_update(event, None, tmp_path)

    assert update.status == "completed"
    assert update.content is not None


def test_permission_ask_options_offers_workspace_and_homedir_for_a_persistable_resource(
    tmp_path: Path,
) -> None:
    resource = PathResource(path=tmp_path / "foo.txt", is_write=False)

    options = permission_ask_options(resource)

    assert [option.option_id for option in options] == [
        "allow:once", "deny:once", "allow:session", "deny:session",
        "allow:workspace", "allow:homedir", "deny:workspace", "deny:homedir"]
    assert [option.kind for option in options] == [
        "allow_once", "reject_once", "allow_always", "reject_always",
        "allow_always", "allow_always", "reject_always", "reject_always"]
    for option in options:
        assert option.field_meta is not None
        assert option.field_meta["klorb"]["scope"] == option.option_id.split(":", 1)[1]


def test_permission_ask_options_omits_workspace_and_homedir_for_a_structural_resource() -> None:
    resource = StructuralResource(reason="couldn't classify this")

    options = permission_ask_options(resource)

    assert [option.option_id for option in options] == [
        "allow:once", "deny:once", "allow:session", "deny:session"]


def test_escalate_privileges_options_offers_exactly_approve_and_deny() -> None:
    options = escalate_privileges_options()

    assert [option.option_id for option in options] == ["allow:once", "deny:once"]
    assert [option.name for option in options] == ["Approve for this session", "Deny"]


def test_permission_ask_tool_call_update_links_the_given_call_id() -> None:
    update = permission_ask_tool_call_update("call-1", "fallback title")

    assert update.tool_call_id == "call-1"
    assert update.title is None


def test_permission_ask_tool_call_update_synthesizes_an_id_when_none_in_flight() -> None:
    update = permission_ask_tool_call_update(None, "fallback title")

    assert update.tool_call_id
    assert update.title == "fallback title"


def test_permission_ask_meta_for_a_non_bash_ask_only_carries_resource_description(tmp_path: Path) -> None:
    resource = PathResource(path=tmp_path / "foo.txt", is_write=False)
    ctx = PermissionAskContext(resource=resource, resource_description="Read foo.txt")
    session_config = SessionConfig(
        model="some/model", workspace_access=WorkspaceAccess(workspace=Workspace(path=tmp_path)))

    meta = permission_ask_meta(ctx, None, 0, 1, session_config)

    assert meta == {"resourceDescription": "Read foo.txt", "headerKind": "Read file"}


def test_permission_ask_meta_for_a_bash_ask_carries_command_and_item_fields(tmp_path: Path) -> None:
    resource = CommandResource(argv=("echo", "hi"))
    bash_context = BashCommandContext(
        command_text="echo hi; echo bye", is_compound=True, item_command_text="echo hi")
    ctx = PermissionAskContext(
        resource=resource, bash_context=bash_context, resource_description="Run command: echo hi")
    session_config = SessionConfig(
        model="some/model", workspace_access=WorkspaceAccess(workspace=Workspace(path=tmp_path)))

    meta = permission_ask_meta(ctx, None, 0, 2, session_config)

    assert meta["resourceDescription"] == "Run command: echo hi"
    assert meta["headerKind"] == "Run command"
    assert meta["commandText"] == "echo hi; echo bye"
    assert meta["itemCommandText"] == "echo hi"
    assert meta["itemIndex"] == 0
    assert meta["itemTotal"] == 2
    assert "riskLevel" not in meta
    assert "riskRationale" not in meta
    # No risk suggestion given -- falls back to the deterministic literal-argv grant pattern.
    assert meta["grantPatterns"] == compute_command_grant_patterns(
        session_config.command_rules, ["echo", "hi"])


def test_permission_ask_meta_prefers_the_risk_classifiers_suggested_pattern(tmp_path: Path) -> None:
    resource = CommandResource(argv=("pytest", "-k", "foo"))
    bash_context = BashCommandContext(command_text="pytest -k foo", item_command_text="pytest -k foo")
    ctx = PermissionAskContext(
        resource=resource, bash_context=bash_context, resource_description="Run pytest")
    risk = ItemRiskAssessment(
        item_id="item-0", risk_score=2, rationale="routine test run",
        suggested_pattern=["pytest", "**"])
    session_config = SessionConfig(
        model="some/model", workspace_access=WorkspaceAccess(workspace=Workspace(path=tmp_path)))

    meta = permission_ask_meta(ctx, risk, 0, 1, session_config)

    assert meta["grantPatterns"] == [["pytest", "**"]]
    assert meta["riskLevel"] == 2
    assert meta["riskRationale"] == "routine test run"


def test_escalate_privileges_meta_carries_scope_and_description(tmp_path: Path) -> None:
    ctx = EscalatePrivilegesContext(
        scope="workspace", description="Grant .klorb/ access", reason="need to write a log file")

    meta = escalate_privileges_meta(ctx)

    assert meta == {
        "escalation": {
            "scope": "workspace",
            "description": "Grant .klorb/ access",
            "reason": "need to write a log file",
        },
    }


def test_permission_ask_meta_omits_origin_session_id_when_unset(tmp_path: Path) -> None:
    resource = PathResource(path=tmp_path / "foo.txt", is_write=False)
    ctx = PermissionAskContext(resource=resource, resource_description="Read foo.txt")
    session_config = SessionConfig(
        model="some/model", workspace_access=WorkspaceAccess(workspace=Workspace(path=tmp_path)))

    meta = permission_ask_meta(ctx, None, 0, 1, session_config)

    assert "originSessionId" not in meta


def test_permission_ask_meta_carries_origin_session_id_for_a_subagent_ask(tmp_path: Path) -> None:
    resource = PathResource(path=tmp_path / "foo.txt", is_write=False)
    ctx = PermissionAskContext(
        resource=resource, resource_description="Read foo.txt", origin_session_id="child-1")
    session_config = SessionConfig(
        model="some/model", workspace_access=WorkspaceAccess(workspace=Workspace(path=tmp_path)))

    meta = permission_ask_meta(ctx, None, 0, 1, session_config)

    assert meta["originSessionId"] == "child-1"


def test_escalate_privileges_meta_carries_origin_session_id_for_a_subagent_ask() -> None:
    ctx = EscalatePrivilegesContext(
        scope="workspace", description="Grant .klorb/ access", reason="need it",
        origin_session_id="child-1")

    meta = escalate_privileges_meta(ctx)

    assert meta["originSessionId"] == "child-1"


_SINGLE_OPTION = [QuestionOption(label="A", description=None, recommended=False)]


def test_ask_user_questions_ext_params_omits_origin_session_id_when_unset() -> None:
    ctx = AskUserQuestionsItemContext(
        header="Pick one", question="Which?", options=_SINGLE_OPTION, index=0, total=1)

    params = ask_user_questions_ext_params(ctx, "session-1")

    assert "originSessionId" not in params


def test_ask_user_questions_ext_params_carries_origin_session_id_for_a_subagent_ask() -> None:
    ctx = AskUserQuestionsItemContext(
        header="Pick one", question="Which?", options=_SINGLE_OPTION, index=0, total=1,
        origin_session_id="child-1")

    params = ask_user_questions_ext_params(ctx, "session-1")

    assert params["originSessionId"] == "child-1"


def test_permission_decision_grant_patterns_only_set_from_risk_suggestion() -> None:
    resource = CommandResource(argv=("git", "status"))
    risk = ItemRiskAssessment(
        item_id="item-0", risk_score=1, rationale="read-only", suggested_pattern=["git", "status"])

    assert permission_decision_grant_patterns(resource, risk) == [["git", "status"]]
    assert permission_decision_grant_patterns(resource, None) is None

    risk_no_pattern = ItemRiskAssessment(
        item_id="item-0", risk_score=1, rationale="read-only", suggested_pattern=[])
    assert permission_decision_grant_patterns(resource, risk_no_pattern) is None


@pytest.mark.parametrize(("option_id", "expected_action", "expected_scope"), [
    ("allow:once", "allow", "once"),
    ("deny:once", "deny", "once"),
    ("allow:session", "allow", "session"),
    ("deny:session", "deny", "session"),
    ("allow:workspace", "allow", "workspace"),
    ("allow:homedir", "allow", "homedir"),
    ("deny:workspace", "deny", "workspace"),
    ("deny:homedir", "deny", "homedir"),
])
def test_permission_decision_from_outcome_round_trips_every_option_id(
    option_id: str, expected_action: str, expected_scope: str,
) -> None:
    outcome = AllowedOutcome(outcome="selected", option_id=option_id)

    decision = permission_decision_from_outcome(outcome, None)

    assert decision.action == expected_action
    assert decision.scope == expected_scope
    assert decision.other_text is None


def test_permission_decision_from_outcome_threads_through_grant_patterns() -> None:
    outcome = AllowedOutcome(outcome="selected", option_id="allow:workspace")

    decision = permission_decision_from_outcome(outcome, [["pytest", "**"]])

    assert decision.grant_patterns == [["pytest", "**"]]


def test_permission_decision_from_outcome_cancelled_denies_once() -> None:
    outcome = DeniedOutcome(outcome="cancelled")

    decision = permission_decision_from_outcome(outcome, None)

    assert decision.action == "deny"
    assert decision.scope == "once"
    assert decision.other_text is None


def test_permission_decision_from_outcome_other_text_overrides_the_selected_option() -> None:
    outcome = AllowedOutcome(
        outcome="selected", option_id="allow:workspace",
        field_meta={"klorb": {"otherText": "use /tmp/scratch instead"}})

    decision = permission_decision_from_outcome(outcome, None)

    assert decision.action == "deny"
    assert decision.scope == "once"
    assert decision.other_text == "use /tmp/scratch instead"


def test_permission_decision_from_outcome_rejects_an_unrecognized_option_id() -> None:
    outcome = AllowedOutcome(outcome="selected", option_id="bogus:once")

    with pytest.raises(ValueError, match="Unrecognized permission option id"):
        permission_decision_from_outcome(outcome, None)


def test_escalate_privileges_decision_from_outcome_approves_only_allow_once() -> None:
    approved = escalate_privileges_decision_from_outcome(
        AllowedOutcome(outcome="selected", option_id="allow:once"))
    denied_option = escalate_privileges_decision_from_outcome(
        AllowedOutcome(outcome="selected", option_id="deny:once"))
    cancelled = escalate_privileges_decision_from_outcome(DeniedOutcome(outcome="cancelled"))

    assert approved.approved is True
    assert denied_option.approved is False
    assert cancelled.approved is False


def test_session_mode_state_reports_all_three_modes_with_the_current_one_selected() -> None:
    state = session_mode_state("auto")

    assert state.available_modes == SESSION_MODES
    assert {mode.id for mode in state.available_modes} == {"ask", "auto", "deny"}
    assert state.current_mode_id == "auto"


def test_session_config_json_reports_current_values_and_every_registered_model(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    config = make_session_config(model="beta", thinking_enabled=False, thinking_effort="low")
    session = Session(config, provider=MagicMock(), model_registry=sample_model_registry())

    payload = session_config_json(session, sample_model_registry())

    assert payload["model"]["current"] == "beta"
    assert payload["model"]["available"] == [
        {"id": "alpha", "name": "alpha"},
        {"id": "beta", "name": "beta"},
        {"id": "gamma", "name": "gamma"},
    ]
    assert payload["thinking"] == {"enabled": False, "effort": "low"}


def test_parse_session_config_update_leaves_unset_fields_none() -> None:
    update = parse_session_config_update({"sessionId": "s1", "model": "gamma"})

    assert update.model == "gamma"
    assert update.thinking is None


def test_parse_session_config_update_parses_thinking_fields() -> None:
    update = parse_session_config_update(
        {"sessionId": "s1", "thinking": {"enabled": True, "effort": "high"}})

    assert update.model is None
    assert update.thinking is not None
    assert update.thinking.enabled is True
    assert update.thinking.effort == "high"


def test_parse_session_config_update_rejects_an_unrecognized_thinking_effort() -> None:
    with pytest.raises(acp.RequestError):
        parse_session_config_update({"sessionId": "s1", "thinking": {"effort": "extreme"}})


def _msg(role: MessageRole, content: str = "", **kwargs: object) -> Message:
    return Message(
        content=content, role=role, num_tokens=1, processing_state="complete",
        timestamp=datetime(2026, 7, 12, 0, 0, 0), **kwargs)  # type: ignore[arg-type]


class TestBuildSessionReplay:
    def test_text_roles_become_text_entries_in_order(
        self, tmp_path: Path, make_session_config: Callable[..., SessionConfig]
    ) -> None:
        session = Session(make_session_config(), provider=MagicMock())
        session.load_messages([
            _msg("user", "hi there"),
            _msg("thinking", "pondering..."),
            _msg("assistant", "hello!"),
        ])

        entries = build_session_replay(session, None, tmp_path)

        assert entries == [
            {"kind": "prompt", "text": "hi there", "streaming": False},
            {"kind": "thinking", "text": "pondering...", "streaming": False},
            {"kind": "response", "text": "hello!", "streaming": False},
        ]

    def test_system_and_tool_defs_messages_are_skipped(
        self, tmp_path: Path, make_session_config: Callable[..., SessionConfig]
    ) -> None:
        session = Session(make_session_config(), provider=MagicMock())
        session.load_messages([
            _msg("system", "you are an agent"),
            _msg("tool_defs", "[]"),
            _msg("user", "hi"),
        ])

        entries = build_session_replay(session, None, tmp_path)

        assert entries == [{"kind": "prompt", "text": "hi", "streaming": False}]

    def test_successful_tool_call_pairs_with_its_response(
        self, tmp_path: Path, make_session_config: Callable[..., SessionConfig]
    ) -> None:
        session = Session(make_session_config(), provider=MagicMock())
        tool_use = _msg("tool_use", tool_calls=[
            ToolCallRequest(id="call-1", name="SampleTool", arguments='{"x": 1}')])
        tool_response = _msg("tool_response", content="42", tool_call_id="call-1")
        session.load_messages([tool_use, tool_response])

        entries = build_session_replay(session, None, tmp_path)

        assert len(entries) == 1
        assert entries[0]["kind"] == "toolCall"
        assert entries[0]["callId"] == "call-1"
        assert entries[0]["status"] == "completed"
        assert entries[0]["contentText"] == "42"

    def test_user_message_after_a_tool_round_replays_as_a_prompt_entry(
        self, tmp_path: Path, make_session_config: Callable[..., SessionConfig],
    ) -> None:
        session = Session(make_session_config(), provider=MagicMock())
        tool_use = _msg("tool_use", tool_calls=[
            ToolCallRequest(id="call-1", name="SampleTool", arguments="{}")])
        tool_response = _msg("tool_response", content="42", tool_call_id="call-1")
        queued_user = _msg("user", "also check the tests")
        session.load_messages([tool_use, tool_response, queued_user])

        entries = build_session_replay(session, None, tmp_path)

        assert entries[0]["kind"] == "toolCall"
        assert entries[0]["callId"] == "call-1"
        assert entries[1] == {
            "kind": "prompt", "text": "also check the tests", "streaming": False}

    def test_error_envelope_response_marks_the_call_failed(
        self, tmp_path: Path, make_session_config: Callable[..., SessionConfig]
    ) -> None:
        session = Session(make_session_config(), provider=MagicMock())
        tool_use = _msg("tool_use", tool_calls=[
            ToolCallRequest(id="call-1", name="SampleTool", arguments="{}")])
        envelope = (
            '{"is_error": true, "is_retryable": false, "error_category": "business_logic", '
            '"error_message": "boom"}')
        tool_response = _msg("tool_response", content=envelope, tool_call_id="call-1")
        session.load_messages([tool_use, tool_response])

        entries = build_session_replay(session, None, tmp_path)

        assert entries[0]["status"] == "failed"
        assert entries[0]["contentText"] == "boom"

    def test_legacy_error_prefix_response_marks_the_call_failed(
        self, tmp_path: Path, make_session_config: Callable[..., SessionConfig]
    ) -> None:
        session = Session(make_session_config(), provider=MagicMock())
        tool_use = _msg("tool_use", tool_calls=[
            ToolCallRequest(id="call-1", name="SampleTool", arguments="{}")])
        tool_response = _msg("tool_response", content="Error: boom", tool_call_id="call-1")
        session.load_messages([tool_use, tool_response])

        entries = build_session_replay(session, None, tmp_path)

        assert entries[0]["status"] == "failed"
        assert entries[0]["contentText"] == "boom"

    def test_tool_call_with_no_matching_response_has_no_content_text(
        self, tmp_path: Path, make_session_config: Callable[..., SessionConfig]
    ) -> None:
        session = Session(make_session_config(), provider=MagicMock())
        tool_use = _msg("tool_use", tool_calls=[
            ToolCallRequest(id="call-1", name="SampleTool", arguments="{}")])
        session.load_messages([tool_use])

        entries = build_session_replay(session, None, tmp_path)

        assert len(entries) == 1
        assert entries[0]["status"] == "completed"
        assert entries[0]["contentText"] is None

    def test_prompt_entry_carries_image_metadata_without_bytes(
        self, tmp_path: Path, make_session_config: Callable[..., SessionConfig]
    ) -> None:
        session = Session(make_session_config(), provider=MagicMock())
        session.load_messages([_msg("user", "look at this", fragments=[
            MessageFragment(type="text", text="look at this"),
            MessageFragment(
                type="image_url", image_url={"url": "data:image/png;base64,xx"},
                source_filename="shot.png", original_width=123, original_height=456),
        ])])

        entries = build_session_replay(session, None, tmp_path)

        assert entries == [{
            "kind": "prompt", "text": "look at this", "streaming": False,
            "images": [{"name": "shot.png", "width": 123, "height": 456}],
        }]

    def test_prompt_entry_image_metadata_omits_unknown_fields_rather_than_nulling_them(
        self, tmp_path: Path, make_session_config: Callable[..., SessionConfig],
    ) -> None:
        session = Session(make_session_config(), provider=MagicMock())
        session.load_messages([_msg("user", "look", fragments=[
            MessageFragment(type="text", text="look"),
            MessageFragment(type="image_url", image_url={"url": "data:image/png;base64,xx"}),
        ])])

        entries = build_session_replay(session, None, tmp_path)

        assert entries[0]["images"] == [{}]

    def test_prompt_entry_without_image_fragments_has_no_images_key(
        self, tmp_path: Path, make_session_config: Callable[..., SessionConfig]
    ) -> None:
        session = Session(make_session_config(), provider=MagicMock())
        session.load_messages([_msg("user", "hi")])

        entries = build_session_replay(session, None, tmp_path)

        assert "images" not in entries[0]

    def test_tool_use_commentary_becomes_a_response_entry_before_its_tool_calls(
        self, tmp_path: Path, make_session_config: Callable[..., SessionConfig],
    ) -> None:
        session = Session(make_session_config(), provider=MagicMock())
        tool_use = _msg("tool_use", content="here's what I found", tool_calls=[
            ToolCallRequest(id="call-1", name="SampleTool", arguments="{}")])
        session.load_messages([tool_use])

        entries = build_session_replay(session, None, tmp_path)

        assert entries[0] == {"kind": "response", "text": "here's what I found", "streaming": False}
        assert entries[1]["kind"] == "toolCall"

    def test_tool_use_with_blank_commentary_emits_no_response_entry(
        self, tmp_path: Path, make_session_config: Callable[..., SessionConfig]
    ) -> None:
        session = Session(make_session_config(), provider=MagicMock())
        tool_use = _msg("tool_use", content="   ", tool_calls=[
            ToolCallRequest(id="call-1", name="SampleTool", arguments="{}")])
        session.load_messages([tool_use])

        entries = build_session_replay(session, None, tmp_path)

        assert len(entries) == 1
        assert entries[0]["kind"] == "toolCall"

    def test_thinking_text_falls_back_to_reasoning_details_when_content_is_empty(
        self, tmp_path: Path, make_session_config: Callable[..., SessionConfig],
    ) -> None:
        session = Session(make_session_config(), provider=MagicMock())
        session.load_messages([_msg(
            "thinking", "", reasoning_details=[{"text": "first"}, {"summary": "second"}])])

        entries = build_session_replay(session, None, tmp_path)

        assert entries == [{"kind": "thinking", "text": "first\n\nsecond", "streaming": False}]

    def test_thinking_text_prefers_content_over_reasoning_details(
        self, tmp_path: Path, make_session_config: Callable[..., SessionConfig]
    ) -> None:
        session = Session(make_session_config(), provider=MagicMock())
        session.load_messages([_msg(
            "thinking", "pondering...", reasoning_details=[{"text": "ignored"}])])

        entries = build_session_replay(session, None, tmp_path)

        assert entries == [{"kind": "thinking", "text": "pondering...", "streaming": False}]

    def test_replayed_readfile_call_includes_readfile_meta(
        self, tmp_path: Path, make_session_config: Callable[..., SessionConfig]
    ) -> None:
        (tmp_path / "hello.txt").write_text("line one\nline two\n")
        session = Session(make_session_config(), provider=MagicMock())
        tool_use = _msg("tool_use", tool_calls=[
            ToolCallRequest(id="call-1", name="ReadFile",
                            arguments='{"filename": "hello.txt"}')])
        tool_response = _msg("tool_response", content=json.dumps({
            "is_error": False, "is_retryable": False, "error_category": None,
            "error_message": None,
            "response_body": {
                "content": "1|line one\n2|line two",
                "filename": "hello.txt", "start_line": 1, "end_line": 2,
                "total_lines": 2, "truncated": False,
            },
        }), tool_call_id="call-1")
        session.load_messages([tool_use, tool_response])

        entries = build_session_replay(session, None, tmp_path)

        assert len(entries) == 1
        assert entries[0]["kind"] == "toolCall"
        assert entries[0]["readFileMeta"]["filename"] == "hello.txt"
        assert "line one" in entries[0]["readFileMeta"]["content"]
        assert "line two" in entries[0]["readFileMeta"]["content"]

    def test_replayed_read_scratchpad_call_includes_readfile_meta(
        self, tmp_path: Path, make_session_config: Callable[..., SessionConfig]
    ) -> None:
        session = Session(make_session_config(), provider=MagicMock())
        tool_use = _msg("tool_use", tool_calls=[
            ToolCallRequest(id="call-1", name="ReadScratchpad", arguments="{}")])
        tool_response = _msg("tool_response", content=json.dumps({
            "is_error": False, "is_retryable": False, "error_category": None,
            "error_message": None,
            "response_body": {
                "content": "1|note", "start_line": 1, "end_line": 1,
                "total_lines": 1, "truncated": False,
            },
        }), tool_call_id="call-1")
        session.load_messages([tool_use, tool_response])

        entries = build_session_replay(session, None, tmp_path)

        assert len(entries) == 1
        assert entries[0]["readFileMeta"]["filename"] == "ReadScratchpad"
        assert "note" in entries[0]["readFileMeta"]["content"]
