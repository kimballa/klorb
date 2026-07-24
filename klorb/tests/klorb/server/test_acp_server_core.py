# © Copyright 2026 Aaron Kimball
"""Tests for `klorb.server.acp_server`/`klorb.server.klorb_agent`/`klorb.server.turn_bridge`:
the ACP server core -- initialize, session/new, session/prompt streaming, session/cancel. See
docs/specs/klorb-server.md."""

import asyncio
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import acp
import pytest
from server.acp_harness import AcpHarness, build_acp_harness

from klorb.api_provider import ApiProvider, ProviderResponse, ResponseAborted
from klorb.message import Message
from klorb.process_config import ProcessConfig
from klorb.workspace import TrustManager


def _reply(content: str = "model reply", num_tokens: int = 5, prompt_tokens: int = 10) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content=content, role="assistant", num_tokens=num_tokens,
            processing_state="complete", timestamp=datetime.now(), finish_reason="stop"),
        prompt_tokens=prompt_tokens,
    )


@pytest.fixture
async def make_harness(tmp_path: Path):
    """Factory fixture: `await make_harness(provider=...)` returns a running `AcpHarness`
    wired to an isolated `TrustManager` (so no test touches the real `KLORB_DATA_DIR`), closed
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


async def test_initialize_echoes_protocol_version_and_klorb_meta(
    make_harness: Callable[..., Any],
) -> None:
    harness = await make_harness(provider=MagicMock())

    response = await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)

    assert response.protocol_version == acp.PROTOCOL_VERSION
    assert response.agent_capabilities is not None
    assert response.agent_capabilities.field_meta == {"klorb": {}}


async def test_new_session_returns_the_live_sessions_id(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)

    response = await harness.client.new_session(cwd=str(tmp_path), mcp_servers=[])

    assert harness.server.agent.session is not None
    assert response.session_id == harness.server.agent.session.id


async def test_second_new_session_closes_the_first_and_returns_a_different_id(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    first_response = await harness.client.new_session(cwd=str(tmp_path), mcp_servers=[])
    first_session = harness.server.agent.session
    assert first_session is not None
    first_session.close = MagicMock(wraps=first_session.close)

    second_response = await harness.client.new_session(cwd=str(tmp_path), mcp_servers=[])

    first_session.close.assert_called_once()
    assert second_response.session_id != first_response.session_id
    assert harness.server.agent.session is not first_session


async def test_prompt_streams_thinking_then_message_chunks_in_order(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None,
    ) -> ProviderResponse:
        assert on_thinking_chunk is not None
        assert on_chunk is not None
        on_thinking_chunk("thinking...")
        on_chunk("hello")
        on_chunk(" world")
        return _reply("hello world")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    harness = await make_harness(provider=mock_provider)
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    session_response = await harness.client.new_session(cwd=str(tmp_path), mcp_servers=[])

    response = await harness.client.prompt(
        session_id=session_response.session_id, prompt=[acp.text_block("hi")])

    assert response.stop_reason == "end_turn"
    updates = harness.harness_client.session_updates
    kinds = [(update.update.session_update, update.update.content.text) for update in updates]
    assert kinds == [
        ("agent_thought_chunk", "thinking..."),
        ("agent_message_chunk", "hello"),
        ("agent_message_chunk", " world"),
    ]


async def test_update_ordering_matches_the_order_callbacks_fired(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    mock_provider = MagicMock()

    def interleaved(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None,
    ) -> ProviderResponse:
        assert on_thinking_chunk is not None
        assert on_chunk is not None
        on_thinking_chunk("t1")
        on_chunk("c1")
        on_thinking_chunk("t2")
        on_chunk("c2")
        return _reply("c1c2")

    mock_provider.send_prompt.side_effect = interleaved
    harness = await make_harness(provider=mock_provider)
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    session_response = await harness.client.new_session(cwd=str(tmp_path), mcp_servers=[])

    await harness.client.prompt(session_id=session_response.session_id, prompt=[acp.text_block("hi")])

    kinds = [
        (update.update.session_update, update.update.content.text)
        for update in harness.harness_client.session_updates
    ]
    assert kinds == [
        ("agent_thought_chunk", "t1"),
        ("agent_message_chunk", "c1"),
        ("agent_thought_chunk", "t2"),
        ("agent_message_chunk", "c2"),
    ]


async def test_cancel_aborts_the_turn_and_keeps_it_in_history(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    mock_provider = MagicMock()
    started = threading.Event()

    def blocking_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None,
    ) -> ProviderResponse:
        assert on_chunk is not None
        assert cancel_event is not None
        on_chunk("partial rep")
        started.set()
        if cancel_event.wait(timeout=5):
            raise ResponseAborted()
        raise AssertionError("cancel_event was never set")

    mock_provider.send_prompt.side_effect = blocking_send_prompt
    harness = await make_harness(provider=mock_provider)
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    session_response = await harness.client.new_session(cwd=str(tmp_path), mcp_servers=[])
    session_id = session_response.session_id

    prompt_task = asyncio.ensure_future(
        harness.client.prompt(session_id=session_id, prompt=[acp.text_block("hi")]))
    for _ in range(500):
        if started.is_set():
            break
        await asyncio.sleep(0.01)
    assert started.is_set()

    await harness.client.cancel(session_id=session_id)
    response = await prompt_task

    assert response.stop_reason == "cancelled"
    session = harness.server.agent.session
    assert session is not None
    user_message = next(m for m in session.messages if m.role == "user")
    assert user_message.processing_state == "aborted"
    assistant_message = next(m for m in session.messages if m.role == "assistant")
    assert assistant_message.processing_state == "aborted"


async def test_prompt_with_wrong_session_id_is_a_json_rpc_error(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    await harness.client.new_session(cwd=str(tmp_path), mcp_servers=[])

    with pytest.raises(acp.RequestError):
        await harness.client.prompt(session_id="not-a-real-session", prompt=[acp.text_block("hi")])


async def test_second_concurrent_prompt_is_a_json_rpc_error(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    mock_provider = MagicMock()
    started = threading.Event()
    release = threading.Event()

    def blocking_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None,
    ) -> ProviderResponse:
        started.set()
        release.wait(timeout=5)
        return _reply("done")

    mock_provider.send_prompt.side_effect = blocking_send_prompt
    harness = await make_harness(provider=mock_provider)
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    session_response = await harness.client.new_session(cwd=str(tmp_path), mcp_servers=[])
    session_id = session_response.session_id

    first_prompt = asyncio.ensure_future(
        harness.client.prompt(session_id=session_id, prompt=[acp.text_block("first")]))
    for _ in range(500):
        if started.is_set():
            break
        await asyncio.sleep(0.01)
    assert started.is_set()

    try:
        with pytest.raises(acp.RequestError):
            await harness.client.prompt(session_id=session_id, prompt=[acp.text_block("second")])
    finally:
        release.set()
        await first_prompt


async def test_eof_stops_the_server_and_closes_the_session(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    await harness.client.new_session(cwd=str(tmp_path), mcp_servers=[])
    session = harness.server.agent.session
    assert session is not None
    session.close = MagicMock(wraps=session.close)

    exit_code = await harness.aclose()

    assert exit_code == 0
    session.close.assert_called_once()
