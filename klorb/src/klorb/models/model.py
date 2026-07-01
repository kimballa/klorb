# © Copyright 2026 Aaron Kimball
"""Abstract interface for a model that klorb can send prompts to."""

from abc import ABC
from abc import abstractmethod
from typing import Any
from typing import Literal

ThinkingBudgetStyle = Literal["effort", "tokens"]
"""How a thinking-capable model wants its reasoning depth controlled: `"effort"` for a
low/medium/high keyword, or `"tokens"` for a numeric reasoning token budget."""


class Model(ABC):
    """Base class describing a model klorb can select and send prompts to."""

    @abstractmethod
    def name(self) -> str:
        """Return the model's identifier, as used by the API provider and the command palette."""

    @abstractmethod
    def system_prompt(self) -> str:
        """Return the system prompt to send to this model."""

    @abstractmethod
    def settings(self) -> dict[str, Any]:
        """Return provider-specific settings/flags to send alongside requests to this model."""

    @abstractmethod
    def capabilities(self) -> dict[str, Any]:
        """Return a dict describing this model's capabilities.

        Standard keys include `vision` (bool), `thinking` (bool), `thinking_budget_style`
        (a `ThinkingBudgetStyle`, meaningful only when `thinking` is `True`), and
        `max_context_window` (int, in tokens), though implementations may include
        additional provider-specific keys.
        """
