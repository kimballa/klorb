# © Copyright 2026 Aaron Kimball
"""Tests for the ACP server's `_klorb/enqueueMessage` extension method and the mid-turn/
turn-end queued-message contract it rides on -- see docs/specs/klorb-server.md's
"Queued messages" section and `klorb.server.turn_bridge.TurnBridge.run_turn`."""

import asyncio
import json
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import acp
import pytest
from server.acp_harness import AcpHarness, build_acp_harness

from klorb.api_provider import ApiProvider, ProviderResponse
from klorb.message import Message, ToolCallRequest
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


def _tool_call_reply(call_id: str, name: str, args: dict[str, Any]) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content="", role="assistant", num_tokens=3, processing_state="complete",
            timestamp=datetime.now(), finish_reason="tool_calls",
            tool_calls=[ToolCallRequest(id=call_id, name=name,
                                        arguments=json.dumps(args) if args else "{}")],
        ),
        prompt_tokens=10,
    )


@pytest.fixture
async def make_harness(tmp_path: Path, make_session_config: Callable[..., SessionConfig]):
    """Factory fixture, mirroring the other `server/` test modules' own: `await make_harness(
    provider=...)` returns a running `AcpHarness` wired to an isolated `TrustManager`, closed
    automatically at teardown if the test hasn't already closed it."""
    harnesses: list[AcpHarness] = []

    async def _make(
        provider: ApiProvider | None = None
    ) -> AcpHarness:
        trust_manager = TrustManager(path=tmp_path / "projects.json")
        harness = await build_acp_harness(ProcessConfig(session=make_session_config()), provider=provider,
            trust_manager=trust_manager)
        harnesses.append(harness)
        return harness

    yield _make

    for harness in harnesses:
        if not harness.server_task.done():
            await harness.aclose()


async def _new_session(
    harness: AcpHarness, cwd: Path, monkeypatch: pytest.MonkeyPatch,
) -> str:
    # The session-naming classifier would otherwise consume the mock provider's scripted
    # send_prompt.side_effect entries too -- short-circuit it so every test's own script lines
    # up with the turn(s) it's actually about.
    monkeypatch.setattr("klorb.session.mixins.core.generate_session_name", lambda *args, **kwargs: None)
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    response = await harness.client.new_session(cwd=str(cwd), mcp_servers=[])
    return str(response.session_id)


async def test_enqueue_message_without_a_turn_in_flight_is_a_json_rpc_error(
    make_harness: Callable[..., Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path, monkeypatch)

    with pytest.raises(acp.RequestError):
        await harness.client.ext_method(
            "klorb/enqueueMessage", {"sessionId": session_id, "text": "hello"})


async def test_enqueue_message_mid_turn_is_delivered_as_a_user_message(
    make_harness: Callable[..., Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enqueuing while a tool-call round is in flight delivers the message as its own
    `role="user"` message immediately after that round's tool_response (not folded into the
    tool_response envelope), bracketed by `_klorb/messageQueued`/`_klorb/queuedMessageSent`
    notifications in order."""
    (tmp_path / "workspace").mkdir()
    workspace = tmp_path / "workspace"
    (workspace / "foo.txt").write_text("hello\n")

    mock_provider = MagicMock()
    started = threading.Event()
    release = threading.Event()
    captured_messages: list[Any] = []

    def send_prompt_side_effect(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
    ) -> ProviderResponse:
        captured_messages.append(list(messages))
        if len(captured_messages) == 1:
            started.set()
            assert release.wait(timeout=5)
            return _tool_call_reply("call_1", "ReadFile", {"filename": "foo.txt"})
        return _reply("done")

    mock_provider.send_prompt.side_effect = send_prompt_side_effect
    harness = await make_harness(provider=mock_provider)
    session_id = await _new_session(harness, workspace, monkeypatch)

    prompt_task = asyncio.ensure_future(
        harness.client.prompt(session_id=session_id, prompt=[acp.text_block("read the file")]))
    for _ in range(500):
        if started.is_set():
            break
        await asyncio.sleep(0.01)
    assert started.is_set()

    result = await harness.client.ext_method(
        "klorb/enqueueMessage", {"sessionId": session_id, "text": "also check the other file"})
    assert result == {"queued": True}

    release.set()
    response = await prompt_task

    assert response.stop_reason == "end_turn"
    # Round 2 (the model's follow-up after the tool call) is what carries both the
    # tool_response and the queued message -- captured_messages[1] is what was actually sent.
    assert len(captured_messages) == 2
    round_2 = captured_messages[1]
    tool_response = next(m for m in round_2 if m.role == "tool_response")
    # The queued message is delivered as its own role="user" message, after the tool_response.
    queued = next(m for m in reversed(round_2) if m.role == "user")
    assert queued.content == "also check the other file"
    assert round_2.index(queued) > round_2.index(tool_response)

    ext_calls = [
        (method, params) for method, params in harness.harness_client.ext_notification_calls
        if method in ("klorb/messageQueued", "klorb/queuedMessageSent")
    ]
    assert [method for method, _ in ext_calls] == ["klorb/messageQueued", "klorb/queuedMessageSent"]
    assert ext_calls[0][1] == {"sessionId": session_id, "text": "also check the other file"}
    assert ext_calls[1][1] == {"sessionId": session_id, "text": "also check the other file"}


async def test_message_still_queued_at_turn_end_becomes_the_next_turn(
    make_harness: Callable[..., Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A message enqueued during a turn that never runs another tool-call round (so it's never
    drained mid-turn) is redelivered as a brand-new turn once the current one ends -- mirroring
    the TUI's `_finish_turn()` -- inside the same `session/prompt` request/response, per
    `TurnBridge.run_turn`'s class docstring."""
    mock_provider = MagicMock()
    started = threading.Event()
    release = threading.Event()
    captured_messages: list[Any] = []

    def send_prompt_side_effect(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
    ) -> ProviderResponse:
        captured_messages.append(list(messages))
        if len(captured_messages) == 1:
            started.set()
            assert release.wait(timeout=5)
            return _reply("first turn done")
        return _reply("second turn done")

    mock_provider.send_prompt.side_effect = send_prompt_side_effect
    harness = await make_harness(provider=mock_provider)
    session_id = await _new_session(harness, tmp_path, monkeypatch)

    prompt_task = asyncio.ensure_future(
        harness.client.prompt(session_id=session_id, prompt=[acp.text_block("first message")]))
    for _ in range(500):
        if started.is_set():
            break
        await asyncio.sleep(0.01)
    assert started.is_set()

    await harness.client.ext_method(
        "klorb/enqueueMessage", {"sessionId": session_id, "text": "queued while busy"})

    release.set()
    response = await prompt_task

    assert response.stop_reason == "end_turn"
    assert len(captured_messages) == 2
    second_turn_user_message = next(m for m in reversed(captured_messages[1]) if m.role == "user")
    assert second_turn_user_message.content == "queued while busy"

    session = harness.server.agent.session
    assert session is not None
    assistant_messages = [m for m in session.messages if m.role == "assistant"]
    assert assistant_messages[-1].content == "second turn done"

    ext_calls = [
        (method, params) for method, params in harness.harness_client.ext_notification_calls
        if method in ("klorb/messageQueued", "klorb/queuedMessageSent")
    ]
    assert [method for method, _ in ext_calls] == ["klorb/messageQueued", "klorb/queuedMessageSent"]
