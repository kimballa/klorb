# © Copyright 2026 Aaron Kimball
"""Client for sending prompts to models hosted on OpenRouter via the OpenAI SDK."""

import os

from openai import OpenAI

from klorb.api_provider import ApiProvider

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY_ENV_VAR = "OPENROUTER_API_KEY"
DEFAULT_MODEL = "openai/gpt-4o-mini"


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
            raise RuntimeError(
                f"{OPENROUTER_API_KEY_ENV_VAR} is not set. Set it to your OpenRouter API key.")
        return api_key

    def build_client(self) -> OpenAI:
        """Construct (and cache) an OpenAI SDK client pointed at the OpenRouter API."""
        if self._client is None:
            self._client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=self.get_api_key())
        return self._client

    def send_prompt(self, prompt: str, model: str | None = None) -> str:
        """Send a single user prompt to a model via OpenRouter and return its text response."""
        client = self.build_client()
        response = client.chat.completions.create(
            model=model or self._model,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""
