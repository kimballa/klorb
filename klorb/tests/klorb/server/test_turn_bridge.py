# © Copyright 2026 Aaron Kimball
"""Tests for klorb.server.turn_bridge.TurnBridge's ordered update pump."""
import asyncio
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

import acp

from klorb.api_provider import ApiProvider, ProviderResponse
from klorb.message import Message
from klorb.process_config import ProcessConfig
from klorb.server.turn_bridge import TurnBridge
from klorb.session import Session, SessionConfig


class _StreamingProvider(ApiProvider):
    """Streams two chunks through `on_chunk` before returning a canned reply."""

    def get_api_key(self) -> str:
        return "fake-key"

    def build_client(self) -> Any:
        return None

    def send_prompt(
        self, messages: list[Message], system_prompt: str | None = None, model: str | None = None,
        session_id: str = "", reasoning: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None, response_format: dict[str, Any] | None = None,
        timeout: float | None = None, drop_reasoning: bool = False, cache_mgmt_style: str = "AUTOMATIC",
        on_chunk: Any = None, on_thinking_chunk: Any = None, on_reasoning_details: Any = None,
        cancel_event: threading.Event | None = None, max_tokens: int | None = None,
    ) -> ProviderResponse:
        if on_chunk is not None:
            on_chunk("the ")
            on_chunk("reply")
        return ProviderResponse(
            message=Message(
                content="the reply", role="assistant", num_tokens=2,
                processing_state="complete", timestamp=datetime.now(), finish_reason="stop"),
            prompt_tokens=5)


class _FailingUpdatesClient:
    """A client whose `session_update` always raises, while extension notifications succeed."""

    def __init__(self) -> None:
        self.update_attempts = 0
        self.ext_notifications: list[tuple[str, dict[str, Any]]] = []

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.update_attempts += 1
        raise ConnectionError("client went away")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        self.ext_notifications.append((method, params))


async def test_run_turn_survives_failing_session_updates(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    """A `session_update` delivery failure must not kill the pump task: the turn still runs to
    completion and returns its reply instead of wedging on a queue nothing drains anymore."""
    session = Session(make_session_config(), provider=_StreamingProvider())
    client = _FailingUpdatesClient()
    bridge = TurnBridge(session, cast(acp.Client, client), session.id, ProcessConfig())

    result = await asyncio.wait_for(bridge.run_turn("hello"), timeout=10.0)

    assert result == "the reply"
    assert client.update_attempts >= 1
    assert any(method == "klorb/usage" for method, _params in client.ext_notifications)
