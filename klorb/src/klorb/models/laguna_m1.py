# © Copyright 2026 Aaron Kimball
"""Model implementation for poolside/laguna-m.1:free, a free OpenRouter coding-agent model."""

from typing import Any

from klorb.models.model import Model

LAGUNA_M1_MODEL_NAME = "poolside/laguna-m.1:free"


class LagunaM1Model(Model):
    """Describes poolside/laguna-m.1:free, Poolside's free coding-agent model on OpenRouter."""

    def name(self) -> str:
        return LAGUNA_M1_MODEL_NAME

    def system_prompt(self) -> str:
        return "You are klorb, a helpful coding and software engineering assistant."

    def settings(self) -> dict[str, Any]:
        return {"temperature": 0.2}

    def capabilities(self) -> dict[str, Any]:
        return {
            "vision": False,
            "thinking": True,
            "thinking_budget_style": "effort",
            "max_context_window": 256_000,
            "max_output_tokens": 32_000,
            "function_calling": True,
            "streaming": True,
        }
