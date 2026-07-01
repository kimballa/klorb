# © Copyright 2026 Aaron Kimball
"""Session state shared by the one-shot prompt path and the interactive REPL."""

import logging
from datetime import datetime

from coolname import generate_slug
from pydantic import BaseModel

from klorb.api_provider import ApiProvider
from klorb.message import Message
from klorb.models.model import Model
from klorb.models.registry import ModelRegistry
from klorb.openrouter import DEFAULT_MODEL
from klorb.openrouter import OpenRouterApiProvider

logger = logging.getLogger(__name__)

# Two-word kebab-case nonce (e.g. "dastardly-happy") to disambiguate session ids
# created within the same minute.
NONCE_WORD_COUNT = 2

SESSION_ID_TIMESTAMP_FORMAT = "%Y-%m-%d-%H-%M"


def generate_session_id() -> str:
    """Generate a session id: yyyy-mm-dd-hh-mm-<nonce>, e.g. '2026-06-30-10-00-happy-otter'."""
    timestamp = datetime.now().strftime(SESSION_ID_TIMESTAMP_FORMAT)
    nonce = generate_slug(NONCE_WORD_COUNT)
    return f"{timestamp}-{nonce}"


class SessionConfig(BaseModel):
    """Configuration for a `Session`, set once at startup from parsed CLI arguments."""

    model: str = DEFAULT_MODEL
    interactive: bool = True


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
        session_id: str | None = None,
    ) -> None:
        self.config = config
        self.id = session_id or generate_session_id()
        self._provider = provider or OpenRouterApiProvider()
        self._model_registry = model_registry or ModelRegistry()
        self._messages: list[Message] = []

    @property
    def provider(self) -> ApiProvider:
        """Return the `ApiProvider` this session sends turns through."""
        return self._provider

    @property
    def model_registry(self) -> ModelRegistry:
        """Return the `ModelRegistry` this session resolves model names against."""
        return self._model_registry

    @property
    def messages(self) -> list[Message]:
        """Return the session's conversation history so far."""
        return list(self._messages)

    def _active_model(self) -> Model | None:
        """Return the registered `Model` for `config.model`, or `None` if it isn't registered."""
        try:
            return self._model_registry.get(self.config.model)
        except KeyError:
            return None

    def active_model_name(self) -> str:
        """Resolve the identifier of the currently configured model.

        Invokes the registered `Model.name()` when `config.model` matches a model
        discovered by the `ModelRegistry`; otherwise returns `config.model` unchanged, so
        callers can still target any OpenRouter model identifier that hasn't been given a
        `Model` implementation yet.
        """
        model = self._active_model()
        return model.name() if model is not None else self.config.model

    def _tokens_recorded_so_far(self) -> int:
        """Sum of `num_tokens` across all messages currently in the buffer."""
        return sum(message.num_tokens for message in self._messages)

    def _dispatch_turn(self, user_message: Message, tokens_before: int) -> str:
        """Send `user_message` (already present in `self._messages`) and mutate it in place.

        On success, `user_message.num_tokens` is set to the delta between this request's
        total `prompt_tokens` and `tokens_before` (the tokens already recorded prior to this
        turn), `processing_state` becomes `"complete"`, `last_error` is cleared, and the
        assistant's reply is appended as a new `Message`. On failure, `user_message` is
        mutated to `processing_state="error"` with `last_error` set, and the exception is
        re-raised.
        """
        model_name = self.active_model_name()
        model = self._active_model()
        system_prompt = model.system_prompt() if model is not None else None
        try:
            result = self._provider.send_prompt(
                list(self._messages), system_prompt=system_prompt, model=model_name, session_id=self.id)
        except Exception as exc:
            user_message.processing_state = "error"
            user_message.last_error = str(exc)
            logger.error("Turn failed for %s: %s", model_name, exc)
            raise

        user_message.num_tokens = result.prompt_tokens - tokens_before
        user_message.processing_state = "complete"
        user_message.last_error = None
        self._messages.append(result.message)
        logger.debug(
            "Turn complete for %s: user num_tokens=%d, assistant num_tokens=%d, finish_reason=%s",
            model_name, user_message.num_tokens, result.message.num_tokens, result.message.finish_reason,
        )
        return result.message.content

    def send_turn(self, prompt: str) -> str:
        """Send one turn of the conversation to the active model and return its response.

        Appends a new user `Message` to the session's history, sends the full history (plus
        the active model's system prompt, if registered) to the provider, and mutates that
        same `Message` in place to reflect the outcome (see `_dispatch_turn`).
        """
        tokens_before = self._tokens_recorded_so_far()
        user_message = Message(
            content=prompt,
            role="user",
            num_tokens=0,
            processing_state="pending",
            timestamp=datetime.now(),
        )
        self._messages.append(user_message)
        logger.info(
            "Sending turn to %s (%d messages in context)", self.active_model_name(), len(self._messages))
        return self._dispatch_turn(user_message, tokens_before)

    def retry_last_turn(self) -> str:
        """Resend the most recently errored user turn's content, mutating that same `Message`.

        Raises `ValueError` if the most recent message isn't a user turn in `"error"` state.
        """
        if not self._messages or self._messages[-1].role != "user" or (
                self._messages[-1].processing_state != "error"):
            raise ValueError("No errored turn to retry.")

        errored_message = self._messages[-1]
        tokens_before = self._tokens_recorded_so_far() - errored_message.num_tokens
        errored_message.processing_state = "pending"
        logger.info("Retrying errored turn for %s", self.active_model_name())
        return self._dispatch_turn(errored_message, tokens_before)

    def run_one_shot(self, prompt: str) -> str:
        """Run a single, non-interactive turn and return the model's text response."""
        return self.send_turn(prompt)
