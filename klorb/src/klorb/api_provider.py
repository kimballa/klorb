# © Copyright 2026 Aaron Kimball
"""Abstract interface for LLM API providers."""

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from klorb.message import Message


class ProviderResponse(BaseModel):
    """The result of one `ApiProvider.send_prompt()` call: the reply plus request-level usage."""

    message: Message
    """The assistant's reply. `message.num_tokens` is the completion-token count for this
    response; `message.finish_reason` is the provider's reported stop reason."""

    prompt_tokens: int
    """Total input tokens billed for this request (covers the full message list sent,
    including the system prompt), as reported by the provider's usage stats."""


class ResponseAborted(Exception):
    """Raised by `ApiProvider.send_prompt()` when `cancel_event` is set while a response is
    streaming in, instead of returning a `ProviderResponse`."""


class ApiProvider(ABC):
    """Base class for clients that send prompts to a hosted LLM API."""

    @abstractmethod
    def get_api_key(self) -> str:
        """Return the API key for this provider, raising if it isn't configured."""

    @abstractmethod
    def build_client(self) -> Any:
        """Construct and return the underlying SDK client for this provider."""

    @abstractmethod
    def send_prompt(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        model: str | None = None,
        session_id: str | None = None,
        reasoning: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        timeout: float | None = None,
        on_chunk: Callable[[str], None] | None = None,
        on_thinking_chunk: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ProviderResponse:
        """Send the given conversation history (plus an optional system prompt) to a model
        and return its reply along with request-level token usage.

        `reasoning`, if given, is a provider-shaped request body requesting extended
        thinking (e.g. `{"effort": "high"}` or `{"max_tokens": 32_768}` for OpenRouter);
        omitted or `None` means no reasoning is requested.

        `tools`, if given, is the OpenAI-style function-calling `tools` array (see
        `klorb.tools.registry.ToolRegistry.tool_definitions`) offered to the model alongside
        the prompt; omitted, `None`, or empty means no tools are offered. If the model
        requests one or more tool calls, they're reported on the returned reply's
        `Message.tool_calls`.

        `response_format`, if given, is an OpenAI-compatible structured-output request body
        (e.g. `{"type": "json_schema", "json_schema": {...}}`) asking the model to reply with
        JSON conforming to a schema, rather than free-form text — see
        `klorb.permissions.risk_classifier.classify_command_risk` for the one caller that uses
        this today. Omitted or `None` means no structured-output constraint is requested.

        `timeout`, if given, bounds this one request's wall-clock time (seconds), overriding
        whatever default timeout the underlying client would otherwise use; omitted or `None`
        means the client's own default applies. Meant for a caller with its own latency budget
        distinct from the main conversation's (e.g. an interactive approval-flow side call that
        must fail fast rather than stall a modal), not for the main conversation turn loop.

        If `on_chunk` is given, it is invoked once per non-empty text delta as the response
        streams in, in addition to the final reply being returned as usual. If
        `on_thinking_chunk` is given, it is invoked once per non-empty reasoning/thinking
        text delta, separately from `on_chunk`.

        If `cancel_event` is given and becomes set while the response is streaming in, the
        provider stops consuming the stream and raises `ResponseAborted` instead of
        returning normally.
        """
