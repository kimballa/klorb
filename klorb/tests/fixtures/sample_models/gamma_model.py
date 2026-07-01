# © Copyright 2026 Aaron Kimball
"""A trivial Model used to test ModelRegistry discovery."""

from typing import Any

from klorb.models.model import Model


class GammaModel(Model):
    """A model with token-budget-style thinking support. Used only in tests."""

    def name(self) -> str:
        return "gamma"

    def system_prompt(self) -> str:
        return "You are Gamma."

    def settings(self) -> dict[str, Any]:
        return {"temperature": 0.0}

    def capabilities(self) -> dict[str, Any]:
        return {
            "vision": False,
            "thinking": True,
            "thinking_budget_style": "tokens",
            "max_context_window": 100_000,
        }
