# © Copyright 2026 Aaron Kimball
"""Command palette provider for toggling thinking on/off and choosing its effort level."""

from functools import partial
from typing import Protocol
from typing import cast

from textual.command import DiscoveryHit
from textual.command import Hit
from textual.command import Hits
from textual.command import Provider

from klorb.session import ThinkingEffort

ENABLE_THINKING_LABEL = "Enable thinking"
DISABLE_THINKING_LABEL = "Disable thinking"
THINKING_EFFORT_LEVELS: tuple[ThinkingEffort, ...] = ("low", "medium", "high")


class SupportsThinkingConfig(Protocol):
    """Structural interface for an App that can have its thinking config changed."""

    def set_thinking_enabled(self, enabled: bool) -> None:
        """Enable or disable extended-thinking requests for subsequent prompts."""

    def set_thinking_effort(self, effort: ThinkingEffort) -> None:
        """Set the reasoning effort level used for subsequent prompts."""


class ThinkingCommandProvider(Provider):
    """Offers thinking on/off and effort-level commands via the command palette."""

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for label, callback in self._commands().items():
            score = matcher.match(label)
            if score > 0:
                yield Hit(score, matcher.highlight(label), callback)

    async def discover(self) -> Hits:
        for label, callback in self._commands().items():
            yield DiscoveryHit(label, callback)

    def _commands(self) -> dict[str, partial[None]]:
        """Return a mapping of command palette display text to its callback."""
        commands: dict[str, partial[None]] = {
            ENABLE_THINKING_LABEL: partial(self._set_thinking_enabled, True),
            DISABLE_THINKING_LABEL: partial(self._set_thinking_enabled, False),
        }
        for effort in THINKING_EFFORT_LEVELS:
            commands[f"Thinking effort: {effort}"] = partial(self._set_thinking_effort, effort)
        return commands

    def _set_thinking_enabled(self, enabled: bool) -> None:
        cast(SupportsThinkingConfig, self.app).set_thinking_enabled(enabled)

    def _set_thinking_effort(self, effort: ThinkingEffort) -> None:
        cast(SupportsThinkingConfig, self.app).set_thinking_effort(effort)
