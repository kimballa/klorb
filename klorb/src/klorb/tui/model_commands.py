# © Copyright 2026 Aaron Kimball
"""Command palette provider that lets the user pick a model from the ModelRegistry."""

from functools import partial
from typing import Protocol
from typing import cast

from textual.command import DiscoveryHit
from textual.command import Hit
from textual.command import Hits
from textual.command import Provider

from klorb.models.registry import ModelRegistry


class SupportsModelSelection(Protocol):
    """Structural interface for an App that can have its active model changed."""

    def select_model(self, name: str) -> None:
        """Make `name` the active model for subsequent prompts."""


class ModelCommandProvider(Provider):
    """Lists models discovered by ModelRegistry, for selection via the command palette."""

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for model_name, command in self._commands().items():
            score = matcher.match(command)
            if score > 0:
                yield Hit(score, matcher.highlight(command), partial(self._select_model, model_name))

    async def discover(self) -> Hits:
        for model_name, command in self._commands().items():
            yield DiscoveryHit(command, partial(self._select_model, model_name))

    def _commands(self) -> dict[str, str]:
        """Return a mapping of model name to its command palette display text."""
        return {model.name(): f"Select model: {model.name()}" for model in ModelRegistry().models()}

    def _select_model(self, name: str) -> None:
        cast(SupportsModelSelection, self.app).select_model(name)
