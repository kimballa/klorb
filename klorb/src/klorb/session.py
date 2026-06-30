# © Copyright 2026 Aaron Kimball
"""Session state shared by the one-shot prompt path and the interactive REPL."""

import logging

from pydantic import BaseModel

from klorb.api_provider import ApiProvider
from klorb.models.registry import ModelRegistry
from klorb.openrouter import DEFAULT_MODEL
from klorb.openrouter import OpenRouterApiProvider

logger = logging.getLogger(__name__)


class SessionConfig(BaseModel):
    """Configuration for a `Session`, set once at startup from parsed CLI arguments."""

    model: str = DEFAULT_MODEL
    interactive: bool = True
    log_filename: str | None = None


class Session:
    """Holds the state for the active klorb session and runs its turn-based loop.

    Both the one-shot prompt path and the interactive REPL send their prompts through a
    `Session`: each call to `send_turn()` is one turn of that loop, regardless of whether
    it's the only turn (one-shot) or one of many (REPL).
    """

    def __init__(
        self,
        config: SessionConfig,
        provider: ApiProvider | None = None,
        model_registry: ModelRegistry | None = None,
    ) -> None:
        self.config = config
        self._provider = provider or OpenRouterApiProvider()
        self._model_registry = model_registry or ModelRegistry()

    def active_model_name(self) -> str:
        """Resolve the identifier of the currently configured model.

        Invokes the registered `Model.name()` when `config.model` matches a model
        discovered by the `ModelRegistry`; otherwise returns `config.model` unchanged, so
        callers can still target any OpenRouter model identifier that hasn't been given a
        `Model` implementation yet.
        """
        try:
            model = self._model_registry.get(self.config.model)
        except KeyError:
            return self.config.model
        return model.name()

    def send_turn(self, prompt: str) -> str:
        """Send one turn of the conversation to the active model and return its response."""
        model_name = self.active_model_name()
        logger.info("Sending turn to %s (%d characters)", model_name, len(prompt))
        response = self._provider.send_prompt(prompt, model=model_name)
        logger.debug("Received %d-character response from %s", len(response), model_name)
        return response

    def run_one_shot(self, prompt: str) -> str:
        """Run a single, non-interactive turn and return the model's text response."""
        return self.send_turn(prompt)
