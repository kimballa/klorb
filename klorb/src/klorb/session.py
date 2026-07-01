# © Copyright 2026 Aaron Kimball
"""Session state shared by the one-shot prompt path and the interactive REPL."""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any
from typing import Literal

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

ThinkingEffort = Literal["low", "medium", "high"]
"""User-facing thinking depth knob, independent of whether the active model wants an
effort keyword or a numeric token budget (see `klorb.models.model.ThinkingBudgetStyle`)."""

THINKING_EFFORT_TOKEN_BUDGETS: dict[ThinkingEffort, int] = {
    "low": 4_096,
    "medium": 16_384,
    "high": 32_768,
}
"""Default reasoning token budgets used for `ThinkingBudgetStyle == "tokens"` models, keyed
by the same `ThinkingEffort` levels offered for `"effort"`-style models, so both kinds of
model share one user-facing knob. See [[map-thinking-effort-levels-to-fixed-token-budgets]].
Overridable per process via `ProcessConfig.thinking_token_budgets`
(see [[process-and-session-config]]); `Session` falls back to this default when none is
given."""


def generate_session_id() -> str:
    """Generate a session id: yyyy-mm-dd-hh-mm-<nonce>, e.g. '2026-06-30-10-00-happy-otter'."""
    timestamp = datetime.now().strftime(SESSION_ID_TIMESTAMP_FORMAT)
    nonce = generate_slug(NONCE_WORD_COUNT)
    return f"{timestamp}-{nonce}"


class SessionConfig(BaseModel):
    """Configuration for a `Session`, set once at startup from parsed CLI arguments."""

    model: str = DEFAULT_MODEL
    interactive: bool = True
    thinking_enabled: bool = True
    thinking_effort: ThinkingEffort = "high"


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
        thinking_token_budgets: dict[ThinkingEffort, int] | None = None,
    ) -> None:
        self.config = config
        self.id = session_id or generate_session_id()
        self._provider = provider or OpenRouterApiProvider()
        self._model_registry = model_registry or ModelRegistry()
        self._thinking_token_budgets = thinking_token_budgets or THINKING_EFFORT_TOKEN_BUDGETS
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
    def thinking_token_budgets(self) -> dict[ThinkingEffort, int]:
        """Return the reasoning token budgets this session looks up `config.thinking_effort`
        in for `"tokens"`-style models (see `_reasoning_params`).
        """
        return self._thinking_token_budgets

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

    def total_tokens_used(self) -> int:
        """Return the running total of tokens consumed by the conversation so far."""
        return self._tokens_recorded_so_far()

    def max_context_window(self) -> int | None:
        """Return the active model's max context window in tokens, or `None` if the
        active model isn't registered or doesn't report one.
        """
        model = self._active_model()
        if model is None:
            return None
        return model.capabilities().get("max_context_window")

    def _reasoning_params(self) -> dict[str, Any] | None:
        """Return the provider-shaped `reasoning` request body for the active turn, or
        `None` if thinking shouldn't be requested (disabled in config, or the active model
        isn't registered or doesn't report `thinking` support). Translates
        `config.thinking_effort` into whichever shape the active model's
        `thinking_budget_style` wants: an `"effort"` keyword, or a numeric `"max_tokens"`
        budget looked up from `THINKING_EFFORT_TOKEN_BUDGETS`.
        """
        if not self.config.thinking_enabled:
            return None
        model = self._active_model()
        if model is None or not model.capabilities().get("thinking", False):
            return None
        budget_style = model.capabilities().get("thinking_budget_style", "effort")
        if budget_style == "tokens":
            return {"max_tokens": self._thinking_token_budgets[self.config.thinking_effort]}
        return {"effort": self.config.thinking_effort}

    def _dispatch_turn(
        self,
        user_message: Message,
        tokens_before: int,
        on_chunk: Callable[[str], None] | None = None,
        on_thinking_chunk: Callable[[str], None] | None = None,
    ) -> str:
        """Send `user_message` (already present in `self._messages`) and mutate it in place.

        Streams the assistant's reply: on the first chunk, a placeholder `Message`
        (`processing_state="started_receipt"`, `streaming_content=[]`) is appended to
        `self._messages`, and each chunk's text is appended to it. On success, that same
        placeholder is finalized in place (`content`, `streaming_content=None`,
        `processing_state="complete"`, `num_tokens`, `finish_reason`) rather than appending a
        second, duplicate assistant `Message`; if no placeholder was ever created (e.g. a
        non-streaming test double), the provider's reply is appended fresh instead.

        Reasoning/thinking deltas (requested via `_reasoning_params()`) are handled the same
        way, but as a separate `role="thinking"` placeholder appended ahead of the assistant
        one, so the two can be told apart and rendered separately. Its `num_tokens` is left
        at `0`: reasoning tokens are already folded into the assistant message's
        `num_tokens` via `result.message.num_tokens`, so giving the thinking message its own
        count would double-count them.

        On success, `user_message.num_tokens` is set to the delta between this request's
        total `prompt_tokens` and `tokens_before` (the tokens already recorded prior to this
        turn), `processing_state` becomes `"complete"`, and `last_error` is cleared. On
        failure, `user_message` is mutated to `processing_state="error"` with `last_error`
        set; if a placeholder was created before the failure, it (and the thinking
        placeholder, if any) is also marked `processing_state="error"`/`last_error` (its
        partial `streaming_content` is left intact), and the exception is re-raised.
        """
        model_name = self.active_model_name()
        model = self._active_model()
        system_prompt = model.system_prompt() if model is not None else None
        reasoning = self._reasoning_params()
        message_snapshot = list(self._messages)

        placeholder: Message | None = None
        thinking_placeholder: Message | None = None

        def handle_chunk(delta_text: str) -> None:
            nonlocal placeholder
            if placeholder is None:
                placeholder = Message(
                    content="",
                    role="assistant",
                    num_tokens=0,
                    processing_state="started_receipt",
                    timestamp=datetime.now(),
                    streaming_content=[],
                )
                self._messages.append(placeholder)
            assert placeholder.streaming_content is not None
            placeholder.streaming_content.append(delta_text)
            if on_chunk is not None:
                on_chunk(delta_text)

        def handle_thinking_chunk(delta_text: str) -> None:
            nonlocal thinking_placeholder
            if thinking_placeholder is None:
                thinking_placeholder = Message(
                    content="",
                    role="thinking",
                    num_tokens=0,
                    processing_state="started_receipt",
                    timestamp=datetime.now(),
                    streaming_content=[],
                )
                self._messages.append(thinking_placeholder)
            assert thinking_placeholder.streaming_content is not None
            thinking_placeholder.streaming_content.append(delta_text)
            if on_thinking_chunk is not None:
                on_thinking_chunk(delta_text)

        try:
            result = self._provider.send_prompt(
                message_snapshot, system_prompt=system_prompt, model=model_name,
                session_id=self.id, reasoning=reasoning, on_chunk=handle_chunk,
                on_thinking_chunk=handle_thinking_chunk)
        except Exception as exc:
            user_message.processing_state = "error"
            user_message.last_error = str(exc)
            if placeholder is not None:
                placeholder.processing_state = "error"
                placeholder.last_error = str(exc)
            if thinking_placeholder is not None:
                thinking_placeholder.processing_state = "error"
                thinking_placeholder.last_error = str(exc)
            logger.error("Turn failed for %s: %s", model_name, exc)
            raise

        user_message.num_tokens = result.prompt_tokens - tokens_before
        user_message.processing_state = "complete"
        user_message.last_error = None
        if thinking_placeholder is not None:
            thinking_placeholder.content = "".join(thinking_placeholder.streaming_content or [])
            thinking_placeholder.streaming_content = None
            thinking_placeholder.processing_state = "complete"
            thinking_placeholder.last_error = None
        if placeholder is not None:
            placeholder.content = result.message.content
            placeholder.streaming_content = None
            placeholder.processing_state = "complete"
            placeholder.last_error = None
            placeholder.num_tokens = result.message.num_tokens
            placeholder.finish_reason = result.message.finish_reason
        else:
            self._messages.append(result.message)
        logger.debug(
            "Turn complete for %s: user num_tokens=%d, assistant num_tokens=%d, finish_reason=%s",
            model_name, user_message.num_tokens, result.message.num_tokens, result.message.finish_reason,
        )
        return result.message.content

    def send_turn(
        self,
        prompt: str,
        on_chunk: Callable[[str], None] | None = None,
        on_thinking_chunk: Callable[[str], None] | None = None,
    ) -> str:
        """Send one turn of the conversation to the active model and return its response.

        Appends a new user `Message` to the session's history, sends the full history (plus
        the active model's system prompt, if registered) to the provider, and mutates that
        same `Message` in place to reflect the outcome (see `_dispatch_turn`). If `on_chunk`
        is given, it is invoked with each text delta as the response streams in; if
        `on_thinking_chunk` is given, it is invoked with each reasoning/thinking text delta.
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
        return self._dispatch_turn(
            user_message, tokens_before, on_chunk=on_chunk, on_thinking_chunk=on_thinking_chunk)

    def retry_last_turn(
        self,
        on_chunk: Callable[[str], None] | None = None,
        on_thinking_chunk: Callable[[str], None] | None = None,
    ) -> str:
        """Resend the most recently errored user turn's content, mutating that same `Message`.

        Scans from the end of the history for the last user-role `Message`; if none exists
        or it isn't in `"error"` state, raises `ValueError`. Any messages after it (e.g. a
        partial assistant placeholder left behind by a failed stream) are discarded before
        resending, since they belong to the failed attempt, not the conversation.
        """
        index = len(self._messages) - 1
        while index >= 0 and self._messages[index].role != "user":
            index -= 1
        if index < 0 or self._messages[index].processing_state != "error":
            raise ValueError("No errored turn to retry.")

        del self._messages[index + 1:]
        errored_message = self._messages[index]
        tokens_before = self._tokens_recorded_so_far() - errored_message.num_tokens
        errored_message.processing_state = "pending"
        logger.info("Retrying errored turn for %s", self.active_model_name())
        return self._dispatch_turn(
            errored_message, tokens_before, on_chunk=on_chunk, on_thinking_chunk=on_thinking_chunk)

    def run_one_shot(
        self,
        prompt: str,
        on_chunk: Callable[[str], None] | None = None,
        on_thinking_chunk: Callable[[str], None] | None = None,
    ) -> str:
        """Run a single, non-interactive turn and return the model's text response."""
        return self.send_turn(prompt, on_chunk=on_chunk, on_thinking_chunk=on_thinking_chunk)
