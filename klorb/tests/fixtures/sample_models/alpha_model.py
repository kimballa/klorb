# © Copyright 2026 Aaron Kimball
"""A trivial Model used to test ModelRegistry discovery."""

from typing import Any

from klorb.models.model import Model


class AlphaModel(Model):
    """A model with vision support. Used only in tests."""

    def name(self) -> str:
        return "alpha"

    def system_prompt(self) -> str:
        return "You are Alpha."

    def settings(self) -> dict[str, Any]:
        return {"temperature": 0.5}

    def capabilities(self) -> dict[str, Any]:
        return {"vision": True, "thinking": False, "max_context_window": 8_000}
