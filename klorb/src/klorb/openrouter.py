# © Copyright 2026 Aaron Kimball
"""Client for sending prompts to models hosted on OpenRouter via the OpenAI SDK."""

import logging
import os

from openai import OpenAI

from klorb.api_provider import ApiProvider

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

    def send_prompt(self, prompt: str, model: str | None = None, session_id: str | None = None) -> str:
        """Send a single user prompt to a model via OpenRouter and return its text response."""
        resolved_model = model or self._model
        logger.info("Sending prompt to %s (%d characters)", resolved_model, len(prompt))
        client = self.build_client()
        extra_body: dict[str, str] | None = {"session_id": session_id} if session_id is not None else None
        response = client.chat.completions.create(
            model=resolved_model,
            messages=[{"role": "user", "content": prompt}],
            extra_body=extra_body,
        )
        content = response.choices[0].message.content or ""
        logger.debug("Received %d-character response from %s", len(content), resolved_model)
        return content
