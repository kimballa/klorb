# © Copyright 2026 Aaron Kimball
"""Model implementation for anthropic/claude-sonnet-5, Anthropic's Sonnet-tier coding
and agentic model, available via OpenRouter."""

from typing import Any

from klorb.models.model import Model

CLAUDE_SONNET_5_MODEL_NAME = "anthropic/claude-sonnet-5"


class ClaudeSonnet5Model(Model):
    """Describes anthropic/claude-sonnet-5 on OpenRouter."""

    def name(self) -> str:
        return CLAUDE_SONNET_5_MODEL_NAME

    def settings(self) -> dict[str, Any]:
        return {"temperature": 0.2}

    def capabilities(self) -> dict[str, Any]:
        return {
            "vision": True,
            "thinking": True,
            "thinking_budget_style": "effort",
            "max_context_window": 1_000_000,
            "max_output_tokens": 128_000,
            "function_calling": True,
            "streaming": True,
        }
