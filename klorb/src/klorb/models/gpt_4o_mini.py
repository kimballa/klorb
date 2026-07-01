# © Copyright 2026 Aaron Kimball
"""Model implementation for openai/gpt-4o-mini, klorb's current default model."""

from typing import Any

from klorb.models.model import Model
from klorb.openrouter import DEFAULT_MODEL


class Gpt4oMiniModel(Model):
    """Describes openai/gpt-4o-mini, the model klorb sends prompts to by default."""

    def name(self) -> str:
        return DEFAULT_MODEL

    def system_prompt(self) -> str:
        return "You are klorb, a helpful coding and software engineering assistant."

    def settings(self) -> dict[str, Any]:
        return {"temperature": 0.2}

    def capabilities(self) -> dict[str, Any]:
        return {
            "vision": True,
            "thinking": False,
            "max_context_window": 128_000,
            "max_output_tokens": 16_384,
            "function_calling": True,
            "streaming": True,
        }
