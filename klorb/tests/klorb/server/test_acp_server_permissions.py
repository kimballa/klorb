# © Copyright 2026 Aaron Kimball
"""Tests for the ACP server's `session/request_permission`/`_klorb/raiseToolCallLimit` surfaces:
`on_permission_ask`/`on_escalate_privileges`/`on_tool_call_limit_reached` wired through
`TurnBridge` against real klorb tools (`CreateFile`, `Bash`, `EscalatePrivileges`, `ReadFile`).
See docs/specs/klorb-server.md."""

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import acp
import pytest
from acp.schema import AllowedOutcome, ClientCapabilities, RequestPermissionResponse
from server.acp_harness import AcpHarness, RecordedPermissionRequest, build_acp_harness

from klorb.api_provider import ApiProvider, ProviderResponse
from klorb.message import Message, ToolCallRequest
from klorb.permissions import risk_classifier as risk_classifier_module
from klorb.permissions.resource import CommandResource
from klorb.permissions.risk_classifier import CommandRiskReport, ItemRiskAssessment
from klorb.permissions.table import PermissionAskItem
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.workspace import TrustManager


def _reply(content: str = "done", num_tokens: int = 3, prompt_tokens: int = 10) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content=content, role="assistant", num_tokens=num_tokens,
            processing_state="complete", timestamp=datetime.now(), finish_reason="stop"),
        prompt_tokens=prompt_tokens,
    )


def _tool_call_reply(calls: list[tuple[str, str, dict[str, Any]]]) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content="", role="assistant", num_tokens=3, processing_state="complete",
            timestamp=datetime.now(), finish_reason="tool_calls",
            tool_calls=[
                ToolCallRequest(id=call_id, name=name, arguments=json.dumps(args))
                for call_id, name, args in calls
            ],
        ),
        prompt_tokens=10,
    )


def _allow_once(option_id: str = "allow:once") -> RequestPermissionResponse:
    return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=option_id))


@pytest.fixture
async def make_harness(tmp_path: Path):
    """Factory fixture, mirroring the other `server/` test modules' own: `await make_harness(
    provider=..., process_config=...)` returns a running `AcpHarness` wired to an isolated
    `TrustManager`, closed automatically at teardown if the test hasn't already closed it."""
    harnesses: list[AcpHarness] = []

    async def _make(
        provider: ApiProvider | None = None, process_config: ProcessConfig | None = None,
    ) -> AcpHarness:
        trust_manager = TrustManager(path=tmp_path / "projects.json")
        harness = await build_acp_harness(
            process_config or ProcessConfig(), provider=provider, trust_manager=trust_manager)
        harnesses.append(harness)
        return harness

    yield _make

    for harness in harnesses:
        if not harness.server_task.done():
            await harness.aclose()


@pytest.mark.parametrize(("option_id", "expect_created"), [
    ("allow:once", True),
    ("allow:session", True),
    ("deny:once", False),
])
async def test_permission_ask_answers_each_option_id(
    make_harness: Callable[..., Any], tmp_path: Path, option_id: str, expect_created: bool,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "CreateFile", {"filename": "new.txt", "content": "hi"})]),
        _reply("done"),
    ]
    harness = await make_harness(provider=mock_provider)
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    session_response = await harness.client.new_session(cwd=str(workspace), mcp_servers=[])

    def answer(request: RecordedPermissionRequest) -> RequestPermissionResponse:
        assert request.tool_call.tool_call_id == "call_1"
        return _allow_once(option_id)

    harness.harness_client.on_request_permission = answer

    response = await harness.client.prompt(
        session_id=session_response.session_id, prompt=[acp.text_block("make a file")])

    assert response.stop_reason == "end_turn"
    assert len(harness.harness_client.permission_requests) == 1
    request = harness.harness_client.permission_requests[0]
    assert "resourceDescription" in request.meta["klorb"]
    assert (workspace / "new.txt").exists() == expect_created
    updates = [n.update for n in harness.harness_client.session_updates]
    finish = next(u for u in updates if u.session_update == "tool_call_update")
    assert finish.status == ("completed" if expect_created else "failed")


async def test_permission_ask_synthesizes_a_tool_call_when_none_in_flight(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    """Defensive path: `permission_ask_tool_call_update` synthesizes a fresh `toolCallId` when
    `TurnBridge`'s in-flight call stack is empty -- exercised here only through the pure-function
    unit test (`test_update_mapping.py`), since every real klorb tool call always has one
    in-flight by the time it can raise an ask. This test instead pins that the stack is populated
    from the real call in flight, i.e. the non-defensive path, end to end."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "CreateFile", {"filename": "new.txt", "content": "hi"})]),
        _reply("done"),
    ]
    harness = await make_harness(provider=mock_provider)
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    session_response = await harness.client.new_session(cwd=str(workspace), mcp_servers=[])
    harness.harness_client.on_request_permission = lambda request: _allow_once()

    await harness.client.prompt(session_id=session_response.session_id, prompt=[acp.text_block("go")])

    assert harness.harness_client.permission_requests[0].tool_call.tool_call_id == "call_1"


async def test_multi_item_bash_ask_sends_serial_requests_and_batches_the_classifier_once(
    make_harness: Callable[..., Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([(
            "call_1", "Bash",
            {"command": "echo a; echo b", "intent": "print two things"})]),
        _reply("done"),
    ]
    harness = await make_harness(provider=mock_provider)
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    session_response = await harness.client.new_session(cwd=str(workspace), mcp_servers=[])

    classify_calls: list[list[PermissionAskItem]] = []

    def fake_classify_command_risk(
        command_text: str, items: list[PermissionAskItem], **kwargs: Any,
    ) -> CommandRiskReport:
        classify_calls.append(items)
        assessments = []
        for index, item in enumerate(items):
            assert isinstance(item.resource, CommandResource)
            assessments.append(ItemRiskAssessment(
                item_id=f"item-{index}", risk_score=1, rationale="routine",
                suggested_pattern=list(item.resource.argv)))
        return CommandRiskReport(
            overall_risk_score=1, overall_rationale="routine", items=assessments)

    monkeypatch.setattr(risk_classifier_module, "classify_command_risk", fake_classify_command_risk)
    harness.harness_client.on_request_permission = lambda request: _allow_once()

    response = await harness.client.prompt(
        session_id=session_response.session_id, prompt=[acp.text_block("run it")])

    assert response.stop_reason == "end_turn"
    requests = harness.harness_client.permission_requests
    assert len(requests) == 2
    assert requests[0].meta["klorb"]["itemIndex"] == 0
    assert requests[0].meta["klorb"]["itemTotal"] == 2
    assert requests[1].meta["klorb"]["itemIndex"] == 1
    assert requests[1].meta["klorb"]["itemTotal"] == 2
    assert requests[0].meta["klorb"]["commandText"] == "echo a; echo b"
    assert requests[0].meta["klorb"]["riskLevel"] == 1
    assert len(classify_calls) == 1
    updates = [n.update for n in harness.harness_client.session_updates]
    finish = next(u for u in updates if u.session_update == "tool_call_update")
    assert finish.status == "completed"


@pytest.mark.parametrize(("option_id", "expected_approved"), [
    ("allow:once", True),
    ("deny:once", False),
])
async def test_escalate_privileges_ask_maps_decision_both_ways(
    make_harness: Callable[..., Any], tmp_path: Path, option_id: str, expected_approved: bool,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply(
            [("call_1", "EscalatePrivileges", {"scope": "workspace", "reason": "need it"})]),
        _reply("done"),
    ]
    harness = await make_harness(provider=mock_provider)
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    session_response = await harness.client.new_session(cwd=str(workspace), mcp_servers=[])

    def answer(request: RecordedPermissionRequest) -> RequestPermissionResponse:
        assert request.meta["klorb"]["escalation"]["scope"] == "workspace"
        assert request.meta["klorb"]["escalation"]["reason"] == "need it"
        return _allow_once(option_id)

    harness.harness_client.on_request_permission = answer

    response = await harness.client.prompt(
        session_id=session_response.session_id, prompt=[acp.text_block("escalate")])

    assert response.stop_reason == "end_turn"
    session = harness.server.agent.session
    assert session is not None
    assert ("workspace" in session.config.approved_scopes) == expected_approved


async def test_tool_call_limit_with_capability_advertised_asks_and_continues(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "f.txt").write_text("hi\n")
    process_config = ProcessConfig(session=SessionConfig(max_tool_calls_per_turn=1))
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([
            ("call_1", "ReadFile", {"filename": "f.txt"}),
            ("call_2", "ReadFile", {"filename": "f.txt"}),
        ]),
        _reply("done"),
    ]
    harness = await make_harness(provider=mock_provider, process_config=process_config)
    await harness.client.initialize(
        protocol_version=acp.PROTOCOL_VERSION,
        client_capabilities=ClientCapabilities(field_meta={"klorb": {"raiseToolCallLimit": True}}))
    session_response = await harness.client.new_session(cwd=str(workspace), mcp_servers=[])

    def answer_ext(method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert method == "klorb/raiseToolCallLimit"
        assert params["sessionId"] == session_response.session_id
        assert "message" in params
        return {"approved": True}

    harness.harness_client.on_ext_method = answer_ext

    response = await harness.client.prompt(
        session_id=session_response.session_id, prompt=[acp.text_block("read twice")])

    assert response.stop_reason == "end_turn"
    assert len(harness.harness_client.ext_method_calls) == 1
    session = harness.server.agent.session
    assert session is not None
    assert session.config.max_tool_calls_per_turn == 2


async def test_tool_call_limit_without_capability_fails_the_turn_and_never_asks(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "f.txt").write_text("hi\n")
    process_config = ProcessConfig(session=SessionConfig(max_tool_calls_per_turn=1))
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([
            ("call_1", "ReadFile", {"filename": "f.txt"}),
            ("call_2", "ReadFile", {"filename": "f.txt"}),
        ]),
        _reply("done"),
    ]
    harness = await make_harness(provider=mock_provider, process_config=process_config)
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    session_response = await harness.client.new_session(cwd=str(workspace), mcp_servers=[])

    with pytest.raises(acp.RequestError):
        await harness.client.prompt(
            session_id=session_response.session_id, prompt=[acp.text_block("read twice")])

    assert harness.harness_client.ext_method_calls == []
