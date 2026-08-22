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
from tools.subagents.conftest import _FakeProvider

from klorb.agents.runtime import SUBAGENT_ABORTED_MARKER, SubagentHandle, SubagentTurnOutcome
from klorb.api_provider import ApiProvider
from klorb.message import Message, MessageRole
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.registry import ToolRegistry
from klorb.workspace import TrustManager


def _add_subagent(
    root: Session, make_session_config: Callable[..., SessionConfig],
    role: str = "explorer", title: str = "find the bug",
) -> SubagentHandle:
    # `session_name=title` mirrors `CreateSubagentTool` pre-setting the child `Session`'s name
    # from `CreateSubagent`'s `session_title` argument -- see docs/specs/subagents.md's
    # "Subagent session model" section.
    child = Session(
        make_session_config(role_name=role), provider=MagicMock(), parent=root, session_name=title)
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
    make_harness: Callable[..., Any], tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)
    root = harness.server.agent.session
    assert root is not None
    handle = _add_subagent(root, make_session_config, role="explorer", title="find the bug")

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
    make_harness: Callable[..., Any], tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)
    root = harness.server.agent.session
    assert root is not None
    handle = _add_subagent(root, make_session_config)
    handle.outcome = SubagentTurnOutcome(
        output=f"partial output\n\n{SUBAGENT_ABORTED_MARKER}", completed=False)

    result = await harness.client.ext_method("klorb/subagentTree", {"sessionId": session_id})

    child_node = result["nodes"][1]
    assert child_node["state"] == "finished"
    assert child_node["aborted"] is True


async def test_subagent_transcript_returns_replayed_entries_and_state(
    make_harness: Callable[..., Any], tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)
    root = harness.server.agent.session
    assert root is not None
    handle = _add_subagent(root, make_session_config)
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
    make_harness: Callable[..., Any], tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)
    root = harness.server.agent.session
    assert root is not None
    handle = _add_subagent(root, make_session_config)
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


async def test_subagent_prompt_unknown_id_is_a_json_rpc_error(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)

    with pytest.raises(acp.RequestError):
        await harness.client.ext_method(
            "klorb/subagentPrompt",
            {"sessionId": session_id, "subagentId": "no-such-id", "text": "hi"})


async def test_subagent_prompt_missing_text_is_a_json_rpc_error(
    make_harness: Callable[..., Any], tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)
    root = harness.server.agent.session
    assert root is not None
    handle = _add_subagent(root, make_session_config)

    with pytest.raises(acp.RequestError):
        await harness.client.ext_method(
            "klorb/subagentPrompt",
            {"sessionId": session_id, "subagentId": handle.session.id})


async def test_subagent_prompt_enqueues_into_a_running_subagent(
    make_harness: Callable[..., Any], tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)
    root = harness.server.agent.session
    assert root is not None
    handle = _add_subagent(root, make_session_config)
    assert handle.state == "running"

    result = await harness.client.ext_method(
        "klorb/subagentPrompt",
        {"sessionId": session_id, "subagentId": handle.session.id, "text": "steer it"})

    assert result == {"mode": "queued"}
    drained = handle.session.drain_queued_messages()
    assert [m.message_text for m in drained] == ["steer it"]
    assert root.subagent_tracker.handles() == [handle]  # no second handle was registered
    assert handle.parent_interested is True  # untouched -- unrelated to this turn's dispatcher


async def test_subagent_prompt_starts_a_fresh_uninterested_turn_on_a_dormant_subagent(
    make_harness: Callable[..., Any], tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    # Unlike `_add_subagent` (built with its own throwaway `MagicMock()` provider and no tool
    # registry, since the other tests in this file only ever inspect a dormant handle, never
    # actually run its turn), this subagent must be able to run a real turn against the
    # harness's own `_FakeProvider` once `dispatch_direct_message` resumes it.
    provider = _FakeProvider(reply_text="direct reply")
    harness = await make_harness(provider=provider)
    session_id = await _new_session(harness, tmp_path)
    root = harness.server.agent.session
    assert root is not None
    child_config = make_session_config(role_name="explorer")
    child = Session(
        child_config, provider=provider, parent=root, session_name="find the bug",
        tool_registry=ToolRegistry.discover_tools(ProcessConfig(), child_config))
    handle = SubagentHandle(
        session=child, thread=threading.Thread(target=lambda: None), cancel_event=threading.Event(),
        role="explorer", title="find the bug",
        outcome=SubagentTurnOutcome(output="earlier output", completed=True))
    root.subagent_tracker.register(handle)

    result = await harness.client.ext_method(
        "klorb/subagentPrompt",
        {"sessionId": session_id, "subagentId": handle.session.id, "text": "poking in directly"})

    assert result == {"mode": "started"}
    new_handle = root.subagent_tracker.handles()[0]
    new_handle.thread.join(timeout=5.0)
    assert new_handle is not handle
    assert new_handle.parent_interested is False
    assert new_handle.output == "direct reply"
    assert root.subagent_tracker.has_undelivered() is False  # uninterested -- never queued


async def test_subagent_prompt_raises_json_rpc_error_when_concurrency_limit_exceeded(
    make_harness: Callable[..., Any], tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)
    harness.server.agent._process_config.subagents_max_concurrent_per_parent = 0
    root = harness.server.agent.session
    assert root is not None
    handle = _add_subagent(root, make_session_config)
    handle.outcome = SubagentTurnOutcome(output="earlier output", completed=True)

    with pytest.raises(acp.RequestError):
        await harness.client.ext_method(
            "klorb/subagentPrompt",
            {"sessionId": session_id, "subagentId": handle.session.id, "text": "hi"})
    # Rejected before anything was dispatched -- the dormant handle is untouched.
    assert root.subagent_tracker.handles() == [handle]
