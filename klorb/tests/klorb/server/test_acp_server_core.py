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
from klorb.session import SessionConfig
from klorb.tools.tasks.common import chainlink_available
from klorb.workspace import TrustManager
from klorb.workspace.session_store import touch_recent_session, write_session_state


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
    assert response.agent_capabilities.field_meta == {"klorb": {
        "sessionConfig": True, "sessionStats": True, "trustWorkspace": True, "reloadSkills": True,
        "enqueueMessage": True, "taskMeta": chainlink_available(), "imageInput": True,
        "subagents": True,
    }}
    assert response.agent_capabilities.load_session is True
    assert response.agent_capabilities.session_capabilities is not None
    assert response.agent_capabilities.session_capabilities.list is not None


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


async def test_list_sessions_returns_saved_sessions_for_workspace(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    write_session_state(workspace, "sess-1", SessionConfig(workspace=workspace), [])
    touch_recent_session(workspace, "sess-1", "sess-1", "Saved session")

    harness = await make_harness(provider=MagicMock())
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)

    response = await harness.client.list_sessions(cwd=str(tmp_path))

    assert len(response.sessions) == 1
    assert response.sessions[0].session_id == "sess-1"
    assert response.sessions[0].title == "Saved session"
    assert response.sessions[0].updated_at is None


async def test_list_sessions_reports_last_modified_timestamp_as_updated_at(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    timestamp = datetime(2026, 7, 19, 1, 50, 0)
    write_session_state(workspace, "sess-1", SessionConfig(workspace=workspace), [])
    touch_recent_session(
        workspace, "sess-1", "sess-1", "Saved session", last_modified_timestamp=timestamp)

    harness = await make_harness(provider=MagicMock())
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)

    response = await harness.client.list_sessions(cwd=str(tmp_path))

    assert response.sessions[0].updated_at == timestamp.isoformat()


async def test_list_sessions_raises_when_cwd_is_omitted(
    make_harness: Callable[..., Any],
) -> None:
    harness = await make_harness(provider=MagicMock())
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)

    with pytest.raises(acp.RequestError):
        await harness.client.list_sessions()


async def test_load_session_replaces_the_live_session_and_restores_messages(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    saved_message = Message(
        content="hi", role="user", num_tokens=1, processing_state="complete",
        timestamp=datetime.now())
    write_session_state(
        workspace, "sess-1", SessionConfig(model="restored/model", workspace=workspace),
        [saved_message], session_id="sess-1", session_name="Restored")
    touch_recent_session(workspace, "sess-1", "sess-1", "Restored")

    harness = await make_harness(provider=MagicMock())
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    await harness.client.new_session(cwd=str(tmp_path), mcp_servers=[])
    live_before = harness.server.agent.session
    assert live_before is not None

    response = await harness.client.load_session(
        cwd=str(tmp_path), mcp_servers=[], session_id="sess-1")

    assert response is not None
    restored_session = harness.server.agent.session
    assert restored_session is not None
    assert restored_session is not live_before
    assert restored_session.id == "sess-1"
    assert restored_session.config.model == "restored/model"
    assert [m.content for m in restored_session.messages] == ["hi"]

    replay_calls = [
        call for call in harness.harness_client.ext_notification_calls
        if call[0] == "klorb/sessionReplay"]
    assert len(replay_calls) == 1
    _, replay_params = replay_calls[0]
    assert replay_params["sessionId"] == "sess-1"
    assert replay_params["entries"] == [{"kind": "prompt", "text": "hi", "streaming": False}]


async def test_load_session_succeeds_when_lookup_matches_an_alias(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    saved_message = Message(
        content="restored", role="user", num_tokens=1, processing_state="complete",
        timestamp=datetime.now())
    # subdir is the original minted id (the directory name); session_id is the renamed id
    write_session_state(
        workspace, "2026-07-28-00-58-automatic-mustang",
        SessionConfig(model="restored/model", workspace=workspace),
        [saved_message], session_id="2026-07-28-fix-auth",
        aliases=["2026-07-28-00-58-automatic-mustang"], session_name="Fix auth")
    touch_recent_session(
        workspace, "2026-07-28-fix-auth", "2026-07-28-00-58-automatic-mustang",
        "Fix auth", aliases=["2026-07-28-00-58-automatic-mustang"])

    harness = await make_harness(provider=MagicMock())
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    await harness.client.new_session(cwd=str(tmp_path), mcp_servers=[])

    response = await harness.client.load_session(
        cwd=str(tmp_path), mcp_servers=[],
        session_id="2026-07-28-00-58-automatic-mustang")

    assert response is not None
    restored_session = harness.server.agent.session
    assert restored_session is not None
    assert restored_session.config.model == "restored/model"
    assert [m.content for m in restored_session.messages] == ["restored"]

    # The ACP session id must be the alias the client passed, not the internal
    # (renamed) id -- every subsequent ext request the client sends will use it.
    assert harness.server.agent._acp_session_id == "2026-07-28-00-58-automatic-mustang"

    replay_calls = [
        call for call in harness.harness_client.ext_notification_calls
        if call[0] == "klorb/sessionReplay"]
    assert len(replay_calls) == 1
    _, replay_params = replay_calls[0]
    assert replay_params["sessionId"] == "2026-07-28-00-58-automatic-mustang"

    # An ext method call using the alias must succeed (not raise "invalid params").
    config = await harness.client.ext_method(
        "klorb/getSessionConfig",
        {"sessionId": "2026-07-28-00-58-automatic-mustang"})
    assert config["model"]["current"] == "restored/model"


async def test_load_session_raises_for_unknown_session_id(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    trust_manager.register_project(tmp_path, trusted=True)
    harness = await make_harness(provider=MagicMock())
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)

    with pytest.raises(acp.RequestError):
        await harness.client.load_session(cwd=str(tmp_path), mcp_servers=[], session_id="nope")


async def test_load_session_raises_when_the_session_is_locked(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    from klorb.lockfile import create_lockfile
    from klorb.workspace.session_store import session_lock_path

    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    write_session_state(workspace, "sess-1", SessionConfig(workspace=workspace), [])
    touch_recent_session(workspace, "sess-1", "sess-1", "Locked session")
    lock = create_lockfile(session_lock_path(workspace, "sess-1"))
    assert lock.try_acquire()
    try:
        harness = await make_harness(provider=MagicMock())
        await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)

        with pytest.raises(acp.RequestError):
            await harness.client.load_session(
                cwd=str(tmp_path), mcp_servers=[], session_id="sess-1")
    finally:
        lock.release()


async def test_prompt_streams_thinking_then_message_chunks_in_order(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
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
    # The session's first turn also fires a `session_info_update` (session naming -- see
    # test_acp_server_session_controls.py) alongside the message/thought chunks this test is
    # about; filtered out here rather than asserted on.
    updates = [
        update.update for update in harness.harness_client.session_updates
        if update.update.session_update in ("agent_thought_chunk", "agent_message_chunk")
    ]
    kinds = [(update.session_update, update.content.text) for update in updates]
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
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
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

    # See test_prompt_streams_thinking_then_message_chunks_in_order for why session_info_update
    # is filtered out here.
    kinds = [
        (update.update.session_update, update.update.content.text)
        for update in harness.harness_client.session_updates
        if update.update.session_update in ("agent_thought_chunk", "agent_message_chunk")
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
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
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


async def test_provider_error_is_handled_gracefully(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = RuntimeError("Provider returned error")

    harness = await make_harness(provider=mock_provider)
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    session_response = await harness.client.new_session(cwd=str(tmp_path), mcp_servers=[])

    response = await harness.client.prompt(
        session_id=session_response.session_id, prompt=[acp.text_block("hello")])

    assert response.stop_reason == "refusal"
    session = harness.server.agent.session
    assert session is not None
    user_message = next(m for m in session.messages if m.role == "user")
    assert user_message.processing_state == "error"
    assert user_message.last_error is not None


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
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
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
