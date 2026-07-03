# © Copyright 2026 Aaron Kimball
"""Model implementation for openai/gpt-5-nano, klorb's current default model."""

from typing import Any

from klorb.models.model import Model
from klorb.openrouter import DEFAULT_MODEL


class Gpt5NanoModel(Model):
    """Describes openai/gpt-5-nano, the model klorb sends prompts to by default."""

    def name(self) -> str:
        return DEFAULT_MODEL

    def settings(self) -> dict[str, Any]:
        return {"temperature": 0.2}

    def capabilities(self) -> dict[str, Any]:
        return {
            "vision": True,
            "thinking": True,
            "thinking_budget_style": "effort",
            "max_context_window": 400_000,
            "max_output_tokens": 128_000,
            "function_calling": True,
            "streaming": True,
        }
