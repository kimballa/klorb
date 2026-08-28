# © Copyright 2026 Aaron Kimball
"""Abstract interface for LLM API providers."""

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from klorb.message import Message
from klorb.models.model import CacheMgmtStyle


class ProviderResponse(BaseModel):
    """The result of one `ApiProvider.send_prompt()` call: the reply plus request-level usage."""

    message: Message
    """The assistant's reply. `message.num_tokens` is a client-side `tiktoken` estimate of
    the reply's own content, not the provider's billed
    completion-token count; `message.finish_reason` is the provider's reported stop reason."""

    prompt_tokens: int
    """Total input tokens billed for this request (covers the full message list sent,
    including the system prompt), as reported by the provider's usage stats. Retained for
    logging/diagnostics only."""

    completion_tokens: int = 0
    """Total output tokens billed for this request (the model's reply tokens), as reported
    by the provider's usage stats. Used for aggregate session-level token tracking in
    `SessionStatistics`."""

    cached_tokens: int = 0
    """Number of input tokens that were served from the provider's prompt cache, as reported
    in the response's usage stats. Zero when the provider doesn't report this metric. Used
    to compute the cache hit ratio for logging/diagnostics."""

    total_cost: float = 0.0
    """Monetary cost of this request as reported by the provider. Zero when the provider
    doesn't report cost. Accumulated across the session in `SessionStatistics`."""

    server_tool_calls: dict[str, int] = Field(default_factory=dict)
    """Per-type server-tool call counts reported in this request's usage block (e.g.
    `{"web_search_requests": 2}`), empty when the provider reports none. Merged into
    `SessionStatistics.server_tool_calls`."""


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
        session_id: str = "",
        reasoning: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        timeout: float | None = None,
        drop_reasoning: bool = False,
        cache_mgmt_style: CacheMgmtStyle = "AUTOMATIC",
        on_chunk: Callable[[str], None] | None = None,
        on_thinking_chunk: Callable[[str], None] | None = None,
        on_reasoning_details: Callable[[list[dict[str, Any]]], None] | None = None,
        cancel_event: threading.Event | None = None,
        max_tokens: int | None = None,
    ) -> ProviderResponse:
        """Send the given conversation history (plus an optional system prompt) to a model
        and return its reply along with request-level token usage.

        `reasoning`, if given, is a provider-shaped request body requesting extended
        thinking; omitted or `None` means no reasoning is requested.

        `tools`, if given, is the OpenAI-style function-calling `tools` array offered to the
        model alongside the prompt; omitted, `None`, or empty means no tools are offered. If
        the model requests one or more tool calls, they're reported on the returned reply's
        `Message.tool_calls`.

        `response_format`, if given, is an OpenAI-compatible structured-output request body
        asking the model to reply with JSON conforming to a schema, rather than free-form text.
        Omitted or `None` means no structured-output constraint is requested.

        `timeout`, if given, bounds this one request's wall-clock time (seconds), overriding
        whatever default timeout the underlying client would otherwise use; omitted or `None`
        means the client's own default applies. Meant for a caller with its own latency budget
        distinct from the main conversation's, not for the main conversation turn loop.

        `drop_reasoning` controls whether prior turns' thinking/reasoning content is included
        in this request at all: preserved and replayed by default, dropped entirely when `True`.

        If `on_chunk` is given, it is invoked once per non-empty text delta as the response
        streams in, in addition to the final reply being returned as usual. If
        `on_thinking_chunk` is given, it is invoked once per non-empty reasoning/thinking
        text delta, separately from `on_chunk`. If `on_reasoning_details` is given, it is
        invoked with the current, fully-merged `reasoning_details` structured payload each
        time a new fragment of it arrives.

        If `cancel_event` is given and becomes set while the response is streaming in, the
        provider stops consuming the stream and raises `ResponseAborted` instead of
        returning normally.

        `max_tokens`, if given, caps the total tokens this one request may generate (reasoning
        plus completion, for a model that bills both against the same budget). Omitted or `None`
        means the model's own default applies.
        """
