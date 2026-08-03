# © Copyright 2026 Aaron Kimball
"""Shared fixtures/helpers for the klorb.tools.subagents test tree."""

import threading
from datetime import datetime
from typing import Any

from klorb.api_provider import ApiProvider, ProviderResponse
from klorb.message import Message


class _FakeProvider(ApiProvider):
    """A provider whose `send_prompt` returns a canned reply synchronously -- fast enough that
    a test can simply `thread.join()` a subagent's background turn rather than polling."""

    def __init__(self, reply_text: str = "subagent reply") -> None:
        self.reply_text = reply_text
        self.calls: list[list[Message]] = []

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
        self.calls.append(list(messages))
        return ProviderResponse(
            message=Message(
                content=self.reply_text, role="assistant", num_tokens=3,
                processing_state="complete", timestamp=datetime.now(), finish_reason="stop"),
            prompt_tokens=5)
