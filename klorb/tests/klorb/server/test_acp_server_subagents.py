# © Copyright 2026 Aaron Kimball
"""Tests for the subagents surface `klorb.server.klorb_agent`/`klorb.server.subagent_updates`
add over ACP: the `subagents` capability flag, and the `_klorb/subagentTree`/
`_klorb/subagentTranscript`/`_klorb/subagentCancel` ext requests. See
docs/specs/subagents.md's "Subagents panel (VSCode)" section."""

import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import acp
import pytest
from server.acp_harness import AcpHarness, build_acp_harness

from klorb.agents.runtime import SUBAGENT_ABORTED_MARKER, SubagentHandle
from klorb.api_provider import ApiProvider
from klorb.message import Message, MessageRole
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.workspace import TrustManager


def _add_subagent(root: Session, role: str = "explorer", title: str = "find the bug") -> SubagentHandle:
    # `session_name=title` mirrors `CreateSubagentTool` pre-setting the child `Session`'s name
    # from `CreateSubagent`'s `session_title` argument -- see docs/specs/subagents.md's
    # "Subagent session model" section.
    child = Session(
        SessionConfig(role_name=role), provider=MagicMock(), parent=root, session_name=title)
    # Started immediately (and left to finish on its own) so `cascade_close_subagents`'s
    # `thread.join()` -- run whenever the harness closes the root session at teardown -- has an
    # already-started thread to join rather than raising on a thread that never ran.
    thread = threading.Thread(target=lambda: None)
    thread.start()
    handle = SubagentHandle(
        session=child, thread=thread, cancel_event=threading.Event(), role=role, title=title)
    root.subagent_tracker.register(handle)
    return handle


def _message(content: str, role: MessageRole = "assistant", num_tokens: int = 1) -> Message:
    return Message(
        content=content, role=role, num_tokens=num_tokens, processing_state="complete",
        timestamp=datetime.now())


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


async def _new_session(harness: AcpHarness, cwd: Path) -> str:
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    response = await harness.client.new_session(cwd=str(cwd), mcp_servers=[])
    return str(response.session_id)


async def test_initialize_advertises_subagents_capability(
    make_harness: Callable[..., Any],
) -> None:
    harness = await make_harness(provider=MagicMock())

    response = await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)

    assert response.agent_capabilities is not None
    assert response.agent_capabilities.field_meta is not None
    assert response.agent_capabilities.field_meta["klorb"]["subagents"] is True


async def test_subagent_tree_reports_only_the_root_by_default(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)

    result = await harness.client.ext_method("klorb/subagentTree", {"sessionId": session_id})

    assert result["nodes"] == [{
        "id": session_id,
        "parentId": None,
        "address": "1",
        "title": None,
        "role": harness.server.agent.session.config.role_name,
        "state": None,
        "aborted": False,
        "model": harness.server.agent.session.config.model,
        "thinkingEnabled": harness.server.agent.session.config.thinking_enabled,
        "thinkingEffort": harness.server.agent.session.config.thinking_effort,
        "usedTokens": 0,
        "maxTokens": harness.server.agent.session.max_context_window(),
        "outputTokens": 0,
    }]


async def test_subagent_tree_includes_a_registered_subagent(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)
    root = harness.server.agent.session
    assert root is not None
    handle = _add_subagent(root, role="explorer", title="find the bug")

    result = await harness.client.ext_method("klorb/subagentTree", {"sessionId": session_id})

    assert [node["id"] for node in result["nodes"]] == [session_id, handle.session.id]
    child_node = result["nodes"][1]
    assert child_node["parentId"] == session_id
    assert child_node["address"] == "1.1"
    assert child_node["title"] == "find the bug"
    assert child_node["role"] == "explorer"
    assert child_node["state"] == "running"
    assert child_node["aborted"] is False


async def test_subagent_tree_reports_aborted_output(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)
    root = harness.server.agent.session
    assert root is not None
    handle = _add_subagent(root)
    handle.state = "finished"
    handle.output = f"partial output\n\n{SUBAGENT_ABORTED_MARKER}"

    result = await harness.client.ext_method("klorb/subagentTree", {"sessionId": session_id})

    child_node = result["nodes"][1]
    assert child_node["state"] == "finished"
    assert child_node["aborted"] is True


async def test_subagent_transcript_returns_replayed_entries_and_state(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)
    root = harness.server.agent.session
    assert root is not None
    handle = _add_subagent(root)
    handle.session.load_messages([
        _message("look into the bug", role="user"),
        _message("found it", role="assistant"),
    ])

    result = await harness.client.ext_method(
        "klorb/subagentTranscript", {"sessionId": session_id, "subagentId": handle.session.id})

    assert result["state"] == "running"
    assert result["aborted"] is False
    assert result["entries"] == [
        {"kind": "prompt", "text": "look into the bug", "streaming": False},
        {"kind": "response", "text": "found it", "streaming": False},
    ]


async def test_subagent_transcript_unknown_id_is_a_json_rpc_error(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)

    with pytest.raises(acp.RequestError):
        await harness.client.ext_method(
            "klorb/subagentTranscript", {"sessionId": session_id, "subagentId": "no-such-id"})


async def test_subagent_cancel_sets_the_handles_cancel_event(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)
    root = harness.server.agent.session
    assert root is not None
    handle = _add_subagent(root)
    assert not handle.cancel_event.is_set()

    result = await harness.client.ext_method(
        "klorb/subagentCancel", {"sessionId": session_id, "subagentId": handle.session.id})

    assert result == {"cancelled": True}
    assert handle.cancel_event.is_set()


async def test_subagent_cancel_unknown_id_is_a_json_rpc_error(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)

    with pytest.raises(acp.RequestError):
        await harness.client.ext_method(
            "klorb/subagentCancel", {"sessionId": session_id, "subagentId": "no-such-id"})
