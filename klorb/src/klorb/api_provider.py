# © Copyright 2026 Aaron Kimball
"""Abstract interface for LLM API providers."""

from abc import ABC
from abc import abstractmethod
from typing import Any


class ApiProvider(ABC):
    """Base class for clients that send prompts to a hosted LLM API."""

    @abstractmethod
    def get_api_key(self) -> str:
        """Return the API key for this provider, raising if it isn't configured."""

    @abstractmethod
    def build_client(self) -> Any:
        """Construct and return the underlying SDK client for this provider."""

    @abstractmethod
    def send_prompt(self, prompt: str, model: str | None = None, session_id: str | None = None) -> str:
        """Send a single prompt to a model and return its text response."""
