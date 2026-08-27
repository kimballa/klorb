# © Copyright 2026 Aaron Kimball
"""Tests for the chat room surface `klorb.server.klorb_agent`/`klorb.server.chat_updates` add
over ACP: the `chat` capability flag, and the `_klorb/chatHistory`/`_klorb/chatPost` ext
requests. See docs/specs/chat-room.md's "VS Code plugin integration" section."""

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import acp
import pytest
from server.acp_harness import AcpHarness, build_acp_harness

from klorb.agents.chat import CHAT_USER_ID
from klorb.api_provider import ApiProvider
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.workspace import TrustManager


@pytest.fixture
async def make_harness(tmp_path: Path, make_session_config: Callable[..., SessionConfig]):
    """Factory fixture: `await make_harness(provider=...)` returns a running `AcpHarness`
    wired to an isolated `TrustManager` (so no test touches the real `KLORB_DATA_DIR`), closed
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


async def _new_session(harness: AcpHarness, cwd: Path) -> str:
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    response = await harness.client.new_session(cwd=str(cwd), mcp_servers=[])
    return str(response.session_id)


async def test_initialize_advertises_chat_capability(
    make_harness: Callable[..., Any],
) -> None:
    harness = await make_harness(provider=MagicMock())

    response = await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)

    assert response.agent_capabilities is not None
    assert response.agent_capabilities.field_meta is not None
    assert response.agent_capabilities.field_meta["klorb"]["chat"] is True


async def test_chat_history_reports_no_messages_and_no_unread_for_a_fresh_channel(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)

    result = await harness.client.ext_method("klorb/chatHistory", {"sessionId": session_id})

    assert result == {"messages": [], "unreadCount": 0, "unreadMentionCount": 0}


async def test_chat_post_appends_a_message_visible_in_the_next_history_poll(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)

    post_result = await harness.client.ext_method(
        "klorb/chatPost", {"sessionId": session_id, "text": "hello from the user"})

    assert post_result["seq"] == 1
    assert post_result["mentions"] == []
    assert post_result["unresolvedMentions"] == []
    history = await harness.client.ext_method("klorb/chatHistory", {"sessionId": session_id})
    assert [m["senderId"] for m in history["messages"]] == [CHAT_USER_ID]
    assert [m["body"] for m in history["messages"]] == ["hello from the user"]


async def test_chat_post_resolves_and_case_corrects_a_self_mention(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    # A self-mention (the poster's own reserved id): `notify_chat_mention` skips it without
    # attempting any wake, so this avoids exercising that side effect here.
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)

    post_result = await harness.client.ext_method(
        "klorb/chatPost", {"sessionId": session_id, "text": "hi @USER"})

    assert post_result["mentions"] == [CHAT_USER_ID]
    history = await harness.client.ext_method("klorb/chatHistory", {"sessionId": session_id})
    assert history["messages"][0]["body"] == "hi @user"


async def test_chat_post_missing_text_is_a_json_rpc_error(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)

    with pytest.raises(acp.RequestError):
        await harness.client.ext_method("klorb/chatPost", {"sessionId": session_id})
