# © Copyright 2026 Aaron Kimball
"""Model implementation for z-ai/glm-5.2, a free OpenRouter coding-agent model."""

from typing import Any

from klorb.models.model import Model

GLM_5_2_MODEL_NAME = "z-ai/glm-5.2"


class Glm52Model(Model):
    """Describes z-ai/glm-5.2, Z.ai's coding-agent model on OpenRouter."""

    def name(self) -> str:
        return GLM_5_2_MODEL_NAME

    def settings(self) -> dict[str, Any]:
        return {"temperature": 0.2}

    def capabilities(self) -> dict[str, Any]:
        return {
            "vision": False,
            "thinking": True,
            "thinking_budget_style": "effort",
            "max_context_window": 1_000_000,
            "max_output_tokens": 131_072,
            "function_calling": True,
            "streaming": True,
        }
