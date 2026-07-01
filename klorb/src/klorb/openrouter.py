# © Copyright 2026 Aaron Kimball
"""Client for sending prompts to models hosted on OpenRouter via the OpenAI SDK."""

import logging
import os
from datetime import datetime
from typing import cast

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from klorb.api_provider import ApiProvider
from klorb.api_provider import ProviderResponse
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
        client: OpenAI | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
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
            logger.debug("Building OpenAI client for OpenRouter at %s", OPENROUTER_BASE_URL)
            self._client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=self.get_api_key())
        return self._client

    def send_prompt(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        model: str | None = None,
        session_id: str | None = None,
    ) -> ProviderResponse:
        """Send the given conversation history to a model via OpenRouter and return its reply."""
        resolved_model = model or self._model
        api_messages = self._build_api_messages(messages, system_prompt)
        logger.info("Sending %d-message turn to %s", len(api_messages), resolved_model)
        client = self.build_client()
        extra_body: dict[str, str] | None = {"session_id": session_id} if session_id is not None else None
        response = client.chat.completions.create(
            model=resolved_model,
            messages=api_messages,
            extra_body=extra_body,
        )
        choice = response.choices[0]
        content = choice.message.content or ""
        usage = response.usage
        completion_tokens = usage.completion_tokens if usage is not None else 0
        prompt_tokens = usage.prompt_tokens if usage is not None else 0
        logger.debug(
            "Received %d-character response from %s (finish_reason=%s, completion_tokens=%d, "
            "prompt_tokens=%d)",
            len(content), resolved_model, choice.finish_reason, completion_tokens, prompt_tokens,
        )
        reply = Message(
            content=content,
            role="assistant",
            num_tokens=completion_tokens,
            processing_state="complete",
            timestamp=datetime.now(),
            finish_reason=choice.finish_reason,
        )
        return ProviderResponse(message=reply, prompt_tokens=prompt_tokens)

    @staticmethod
    def _build_api_messages(
        messages: list[Message], system_prompt: str | None
    ) -> list[ChatCompletionMessageParam]:
        """Convert session history (plus an optional system prompt) into OpenAI SDK message dicts."""
        api_messages: list[ChatCompletionMessageParam] = []
        if system_prompt is not None:
            system_message = {"role": "system", "content": system_prompt}
            api_messages.append(cast(ChatCompletionMessageParam, system_message))
        api_messages.extend(
            cast(ChatCompletionMessageParam, {"role": message.role, "content": message.content})
            for message in messages
        )
        return api_messages
