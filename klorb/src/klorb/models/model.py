# © Copyright 2026 Aaron Kimball
"""Abstract interface for a model that klorb can send prompts to."""

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel

from klorb.system_prompt import mangle_model_name, resolve_prompt_file

ThinkingBudgetStyle = Literal["effort", "tokens"]
"""How a thinking-capable model wants its reasoning depth controlled: `"effort"` for a
low/medium/high keyword, or `"tokens"` for a numeric reasoning token budget."""


class ModelPricing(BaseModel):
    """A model's published cost per million tokens ("MTok") sent/received, e.g. as reported by
    an OpenRouter model listing."""

    input_cost_per_mtok: float
    output_cost_per_mtok: float
    currency: str = "USD"


class Model(ABC):
    """Base class describing a model klorb can select and send prompts to."""

    @abstractmethod
    def name(self) -> str:
        """Return the model's identifier, as used by the API provider and the command palette."""

    def mangled_name(self) -> str:
        """Return `name()` made filesystem-safe (see
        `klorb.system_prompt.mangle_model_name`): this model's filename stem within a
        `system_prompts.d/` prompt-file tree."""
        return mangle_model_name(self.name())

    def system_prompt(self) -> str | None:
        """Return this model's role-agnostic, model-specific system prompt — the
        `<mangled_name()>.md` prompt file at the top of a `system_prompts.d/` tree (user
        override tier, then packaged tier — see
        `klorb.system_prompt.resolve_prompt_file`) — or `None` if no such file exists.
        Consulted by `Session._resolve_system_prompt()` as one tier of its "default walk",
        independent of whatever the active `Role`'s own prompt tiers resolve. Subclasses
        (e.g. test fixtures) may override to return a literal string without filesystem
        access."""
        return resolve_prompt_file(f"{self.mangled_name()}.md")

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

    def family(self) -> str | None:
        """Return this model's family/tier identifier (e.g. `"sonnet"`, `"glm"`), or `None`
        if unknown.

        A model's `name()` is often an OpenRouter identifier that conflates a family name
        with its version number (e.g. `"anthropic/claude-sonnet-5"` mixes the `"sonnet"`
        tier with version `"5"`); `family()` and `model_version()` tease those apart so a
        caller can detect that e.g. `"anthropic/claude-sonnet-5"` and a future
        `"anthropic/claude-sonnet-5.1"` share a family even though their full names differ.
        """
        return None

    def model_version(self) -> str | None:
        """Return this model's version within its `family()` (e.g. `"5.0"`), or `None` if
        unknown. See `family()`."""
        return None

    def pricing(self) -> ModelPricing | None:
        """Return this model's published per-token cost, or `None` if unknown/not published."""
        return None
