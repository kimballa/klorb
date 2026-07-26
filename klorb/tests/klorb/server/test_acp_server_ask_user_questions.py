# © Copyright 2026 Aaron Kimball
"""Tests for the ACP server's `_klorb/askUserQuestions` extension method: `on_ask_user_questions`
wired through `TurnBridge` against a real `AskUserQuestionsTool` call. See
docs/specs/klorb-server.md."""

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import acp
import pytest
from acp.schema import ClientCapabilities
from server.acp_harness import AcpHarness, build_acp_harness

from klorb.api_provider import ApiProvider, ProviderResponse
from klorb.message import Message, ToolCallRequest
from klorb.process_config import ProcessConfig
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


_THREE_QUESTIONS = {
    "questions": [
        {
            "header": "Format",
            "question": "Which format?",
            "options": [{"label": "JSON"}, {"label": "YAML", "description": "human-friendly"}],
        },
        {"header": "Name", "question": "What should we call it?", "options": []},
        {
            "header": "Ship it?",
            "question": "Ready to ship?",
            "options": [{"label": "Yes"}, {"label": "No"}],
        },
    ],
}

_ASK_CAPABLE = ClientCapabilities(field_meta={"klorb": {"askUserQuestions": True}})


@pytest.fixture
async def make_harness(tmp_path: Path):
    """Factory fixture, mirroring the other `server/` test modules' own: `await make_harness(
    provider=...)` returns a running `AcpHarness` wired to an isolated `TrustManager`, closed
    automatically at teardown if the test hasn't already closed it."""
    harnesses: list[AcpHarness] = []

    async def _make(provider: ApiProvider | None = None) -> AcpHarness:
        trust_manager = TrustManager(path=tmp_path / "projects.json")
        harness = await build_acp_harness(ProcessConfig(), provider=provider, trust_manager=trust_manager)
        harnesses.append(harness)
        return harness

    yield _make

    for harness in harnesses:
        if not harness.server_task.done():
            await harness.aclose()


async def test_answers_each_question_by_selected_option(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "AskUserQuestions", _THREE_QUESTIONS)]),
        _reply("done"),
    ]
    harness = await make_harness(provider=mock_provider)
    await harness.client.initialize(
        protocol_version=acp.PROTOCOL_VERSION, client_capabilities=_ASK_CAPABLE)
    session_response = await harness.client.new_session(cwd=str(workspace), mcp_servers=[])

    def answer_ext(method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert method == "klorb/askUserQuestions"
        assert params["sessionId"] == session_response.session_id
        if params["header"] == "Format":
            assert params["options"] == [
                {"label": "JSON"}, {"label": "YAML", "description": "human-friendly"}]
            assert params["index"] == 0
            assert params["total"] == 3
            return {"selectedOptionIndex": 1}
        if params["header"] == "Name":
            assert params["options"] == []
            assert params["index"] == 1
            assert params["total"] == 3
            return {"otherText": "widget"}
        assert params["header"] == "Ship it?"
        assert params["index"] == 2
        assert params["total"] == 3
        return {"selectedOptionIndex": 0}

    harness.harness_client.on_ext_method = answer_ext

    response = await harness.client.prompt(
        session_id=session_response.session_id, prompt=[acp.text_block("ask away")])

    assert response.stop_reason == "end_turn"
    assert len(harness.harness_client.ext_method_calls) == 3
    updates = [n.update for n in harness.harness_client.session_updates]
    finish = next(u for u in updates if u.session_update == "tool_call_update")
    assert finish.status == "completed"
    detail_text = finish.content[0].content.text
    assert "YAML: human-friendly" in detail_text
    assert "widget" in detail_text
    assert "  -> Yes" in detail_text


async def test_cancelled_answer_short_circuits_the_remaining_questions(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    """A `cancelled` answer to any one question fails the whole `AskUserQuestions` call (a
    `business_logic` `ToolCallOutcome`, like a denied permission ask) rather than raising -- the
    model still gets a turn to react to the failure, and no later question is ever asked."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "AskUserQuestions", _THREE_QUESTIONS)]),
        _reply("acknowledged"),
    ]
    harness = await make_harness(provider=mock_provider)
    await harness.client.initialize(
        protocol_version=acp.PROTOCOL_VERSION, client_capabilities=_ASK_CAPABLE)
    session_response = await harness.client.new_session(cwd=str(workspace), mcp_servers=[])

    def answer_ext(method: str, params: dict[str, Any]) -> dict[str, Any]:
        if params["header"] == "Format":
            return {"selectedOptionIndex": 0}
        return {"cancelled": True}

    harness.harness_client.on_ext_method = answer_ext

    response = await harness.client.prompt(
        session_id=session_response.session_id, prompt=[acp.text_block("ask away")])

    assert response.stop_reason == "end_turn"
    assert len(harness.harness_client.ext_method_calls) == 2
    updates = [n.update for n in harness.harness_client.session_updates]
    finish = next(u for u in updates if u.session_update == "tool_call_update")
    assert finish.status == "failed"


async def test_capability_absent_answers_cancelled_with_no_wire_traffic(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "AskUserQuestions", _THREE_QUESTIONS)]),
        _reply("acknowledged"),
    ]
    harness = await make_harness(provider=mock_provider)
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    session_response = await harness.client.new_session(cwd=str(workspace), mcp_servers=[])

    response = await harness.client.prompt(
        session_id=session_response.session_id, prompt=[acp.text_block("ask away")])

    assert response.stop_reason == "end_turn"
    assert harness.harness_client.ext_method_calls == []
    updates = [n.update for n in harness.harness_client.session_updates]
    finish = next(u for u in updates if u.session_update == "tool_call_update")
    assert finish.status == "failed"
