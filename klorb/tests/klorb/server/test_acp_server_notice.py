# © Copyright 2026 Aaron Kimball
"""Tests for the ACP server's `_klorb/notice` extension notification."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import acp
import pytest
from server.acp_harness import AcpHarness, build_acp_harness

from klorb.hooks.config import HookConfig
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.workspace import TrustManager


@pytest.fixture(autouse=True)
def _unsandboxed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("klorb.hooks.bash_handler.bwrap_available", lambda: False)


@pytest.fixture
async def make_harness(tmp_path: Path, make_session_config: Callable[..., SessionConfig]):
    harnesses: list[AcpHarness] = []

    async def _make(process_config: ProcessConfig) -> AcpHarness:
        trust_manager = TrustManager(path=tmp_path / "projects.json")
        harness = await build_acp_harness(process_config, trust_manager=trust_manager)
        harnesses.append(harness)
        return harness

    yield _make

    for harness in harnesses:
        if not harness.server_task.done():
            await harness.aclose()


async def test_new_session_sends_klorb_notice_for_a_hooks_log(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    process_config = ProcessConfig(hooks={
        "onSessionStart": [HookConfig(type="bash", shell='echo \'{"log": "debug note"}\'')],
    })
    harness = await make_harness(process_config)

    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    response = await harness.client.new_session(cwd=str(tmp_path), mcp_servers=[])

    # `_wire_session_notice_handler`'s callback schedules `_send_notice` via
    # `asyncio.run_coroutine_threadsafe` rather than awaiting it inline, so it
    # may not have run yet the instant `new_session()` returns.
    notices: list[dict[str, Any]] = []
    for _ in range(500):
        notices = [
            params for method, params in harness.harness_client.ext_notification_calls
            if method == "klorb/notice"
        ]
        if notices:
            break
        await asyncio.sleep(0.01)
    assert notices == [{"sessionId": str(response.session_id), "text": "debug note"}]
