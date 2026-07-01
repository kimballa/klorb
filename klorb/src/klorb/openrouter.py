# © Copyright 2026 Aaron Kimball
"""Client for sending prompts to models hosted on OpenRouter via the OpenAI SDK."""

import logging
import os
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any
from typing import cast

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from klorb.api_provider import ApiProvider
from klorb.api_provider import ProviderResponse
from klorb.api_provider import ResponseAborted
from klorb.message import Message

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY_ENV_VAR = "OPENROUTER_API_KEY"
DEFAULT_MODEL = "openai/gpt-4o-mini"

logger = logging.getLogger(__name__)


class OpenRouterApiProvider(ApiProvider):
    """Sends prompts to OpenRouter-hosted models using the OpenAI SDK."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        base_url: str = OPENROUTER_BASE_URL,
        client: OpenAI | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._client = client

    def get_api_key(self) -> str:
        """Read the OpenRouter API key, raising if it isn't configured."""
        api_key = self._api_key or os.environ.get(OPENROUTER_API_KEY_ENV_VAR)
        if not api_key:
            logger.error("%s is not set; cannot authenticate with OpenRouter.", OPENROUTER_API_KEY_ENV_VAR)
            raise RuntimeError(
                f"{OPENROUTER_API_KEY_ENV_VAR} is not set. Set it to your OpenRouter API key.")
        return api_key

    def build_client(self) -> OpenAI:
        """Construct (and cache) an OpenAI SDK client pointed at the OpenRouter API."""
        if self._client is None:
            logger.debug("Building OpenAI client for OpenRouter at %s", self._base_url)
            self._client = OpenAI(base_url=self._base_url, api_key=self.get_api_key())
        return self._client

    def send_prompt(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        model: str | None = None,
        session_id: str | None = None,
        reasoning: dict[str, Any] | None = None,
        on_chunk: Callable[[str], None] | None = None,
        on_thinking_chunk: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ProviderResponse:
        """Send the given conversation history to a model via OpenRouter, streaming the
        reply and invoking `on_chunk` per text delta and `on_thinking_chunk` per
        reasoning/thinking delta, and return the fully-assembled reply.

        If `cancel_event` is given and set while the stream is being consumed, the
        underlying HTTP stream is closed and `ResponseAborted` is raised.
        """
        resolved_model = model or self._model
        api_messages = self._build_api_messages(messages, system_prompt)
        logger.info("Sending %d-message turn to %s", len(api_messages), resolved_model)
        client = self.build_client()
        extra_body: dict[str, Any] | None = None
        if session_id is not None or reasoning is not None:
            extra_body = {}
            if session_id is not None:
                extra_body["session_id"] = session_id
            if reasoning is not None:
                extra_body["reasoning"] = reasoning
        stream = client.chat.completions.create(
            model=resolved_model,
            messages=api_messages,
            stream=True,
            stream_options={"include_usage": True},
            extra_body=extra_body,
        )

        content_parts: list[str] = []
        finish_reason: str | None = None
        completion_tokens = 0
        prompt_tokens = 0
        for chunk in stream:
            if cancel_event is not None and cancel_event.is_set():
                stream.close()
                logger.info("Aborted streamed response from %s", resolved_model)
                raise ResponseAborted()
            if chunk.choices:
                choice = chunk.choices[0]
                delta_text = choice.delta.content or ""
                if delta_text:
                    content_parts.append(delta_text)
                    if on_chunk is not None:
                        on_chunk(delta_text)
                thinking_delta_text = getattr(choice.delta, "reasoning", None) or ""
                if thinking_delta_text and on_thinking_chunk is not None:
                    on_thinking_chunk(thinking_delta_text)
                if choice.finish_reason is not None:
                    finish_reason = choice.finish_reason
            if chunk.usage is not None:
                completion_tokens = chunk.usage.completion_tokens
                prompt_tokens = chunk.usage.prompt_tokens

        content = "".join(content_parts)
        logger.debug(
            "Received %d-character response from %s (finish_reason=%s, completion_tokens=%d, "
            "prompt_tokens=%d)",
            len(content), resolved_model, finish_reason, completion_tokens, prompt_tokens,
        )
        reply = Message(
            content=content,
            role="assistant",
            num_tokens=completion_tokens,
            processing_state="complete",
            timestamp=datetime.now(),
            finish_reason=finish_reason,
        )
        return ProviderResponse(message=reply, prompt_tokens=prompt_tokens)

    @staticmethod
    def _build_api_messages(
        messages: list[Message], system_prompt: str | None
    ) -> list[ChatCompletionMessageParam]:
        """Convert session history (plus an optional system prompt) into OpenAI SDK message
        dicts. `"thinking"`-role messages are omitted: they aren't a role the OpenAI-compatible
        API accepts, and reasoning content isn't meant to be replayed as conversation history.
        """
        api_messages: list[ChatCompletionMessageParam] = []
        if system_prompt is not None:
            system_message = {"role": "system", "content": system_prompt}
            api_messages.append(cast(ChatCompletionMessageParam, system_message))
        api_messages.extend(
            cast(ChatCompletionMessageParam, {"role": message.role, "content": message.content})
            for message in messages
            if message.role != "thinking"
        )
        return api_messages
