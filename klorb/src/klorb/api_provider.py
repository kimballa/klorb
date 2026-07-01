# © Copyright 2026 Aaron Kimball
"""Abstract interface for LLM API providers."""

from abc import ABC
from abc import abstractmethod
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
        on_chunk: Callable[[str], None] | None = None,
    ) -> ProviderResponse:
        """Send the given conversation history (plus an optional system prompt) to a model
        and return its reply along with request-level token usage.

        If `on_chunk` is given, it is invoked once per non-empty text delta as the response
        streams in, in addition to the final reply being returned as usual.
        """
