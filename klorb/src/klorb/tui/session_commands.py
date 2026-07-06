# © Copyright 2026 Aaron Kimball
"""Command palette provider that lets the user clear the active session."""

from typing import Protocol, cast

from textual.command import DiscoveryHit, Hit, Hits, Provider

CLEAR_SESSION_LABEL = "Clear session"


class SupportsSessionClear(Protocol):
    """Structural interface for an App that can clear its active session."""

    def clear_session(self) -> None:
        """Discard the active session's history and start a fresh one."""


class SessionCommandProvider(Provider):
    """Offers session-management commands (currently just clearing the active session) via
    the command palette — reachable via `ctrl+p` or by typing `>clear` in the prompt (see
    `docs/specs/command-palette-from-prompt.md`).
    """

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        score = matcher.match(CLEAR_SESSION_LABEL)
        if score > 0:
            yield Hit(score, matcher.highlight(CLEAR_SESSION_LABEL), self._clear_session)

    async def discover(self) -> Hits:
        yield DiscoveryHit(CLEAR_SESSION_LABEL, self._clear_session)

    def _clear_session(self) -> None:
        cast(SupportsSessionClear, self.app).clear_session()
