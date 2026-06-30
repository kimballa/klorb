# © Copyright 2026 Aaron Kimball
"""A trivial Model used to test ModelRegistry discovery."""

from typing import Any

from klorb.models.model import Model


class BetaModel(Model):
    """A model with thinking support. Used only in tests."""

    def name(self) -> str:
        return "beta"

    def system_prompt(self) -> str:
        return "You are Beta."

    def settings(self) -> dict[str, Any]:
        return {"temperature": 0.0}

    def capabilities(self) -> dict[str, Any]:
        return {"vision": False, "thinking": True, "max_context_window": 200_000}
