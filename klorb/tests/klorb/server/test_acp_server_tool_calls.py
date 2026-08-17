# © Copyright 2026 Aaron Kimball
"""Tests for the ACP server's tool-call `session/update` notifications -- `tool_call`/
`tool_call_update` mapping wired through `TurnBridge`/`klorb.server.update_mapping` against a
real `ToolRegistry`. See docs/specs/klorb-server.md."""

import json
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


@pytest.fixture
async def make_harness(tmp_path: Path, make_session_config: Callable[..., SessionConfig]):
    """Factory fixture, mirroring `test_acp_server_core.py`'s own: `await make_harness(provider=
    ...)` returns a running `AcpHarness` wired to an isolated `TrustManager`, closed
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


async def test_tool_calls_emit_ordered_started_and_finished_updates_matched_by_call_id(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    (tmp_path / "workspace").mkdir()
    workspace = tmp_path / "workspace"
    (workspace / "foo.txt").write_text("hello\n")

    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([
            ("call_1", "ReadFile", {"filename": "foo.txt"}),
            ("call_2", "ReadFile", {"filename": "missing.txt"}),
        ]),
        _reply("done"),
    ]
    harness = await make_harness(provider=mock_provider)
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    session_response = await harness.client.new_session(cwd=str(workspace), mcp_servers=[])

    response = await harness.client.prompt(
        session_id=session_response.session_id, prompt=[acp.text_block("read two files")])

    assert response.stop_reason == "end_turn"
    # The session's first turn also fires a `session_info_update` (session naming -- see
    # test_acp_server_session_controls.py); filtered out here since this test is only about
    # tool-call update ordering.
    updates = [
        notification.update for notification in harness.harness_client.session_updates
        if notification.update.session_update != "session_info_update"
    ]
    assert [update.session_update for update in updates] == [
        "tool_call", "tool_call_update", "tool_call", "tool_call_update"]

    first_start, first_finish, second_start, second_finish = updates

    assert first_start.tool_call_id == "call_1"
    assert first_start.status == "in_progress"
    assert first_start.kind == "read"
    assert first_start.locations is not None
    assert first_start.locations[0].path == str((workspace / "foo.txt").resolve())

    assert first_finish.tool_call_id == "call_1"
    assert first_finish.status == "completed"
    assert first_finish.content is not None
    assert "foo.txt" in first_finish.content[0].content.text

    assert second_start.tool_call_id == "call_2"
    assert second_start.status == "in_progress"
    assert second_start.kind == "read"

    assert second_finish.tool_call_id == "call_2"
    assert second_finish.status == "failed"
    assert second_finish.content is not None
    assert "missing.txt" in second_finish.content[0].content.text
