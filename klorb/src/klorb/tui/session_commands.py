# © Copyright 2026 Aaron Kimball
"""Command palette provider that lets the user clear the active session."""

from typing import Protocol
from typing import cast

from textual.command import DiscoveryHit
from textual.command import Hit
from textual.command import Hits
from textual.command import Provider

CLEAR_SESSION_COMMAND = "/clear"
CLEAR_SESSION_LABEL = "Clear session"


class SupportsSessionClear(Protocol):
    """Structural interface for an App that can clear its active session."""

    def clear_session(self) -> None:
        """Discard the active session's history and start a fresh one."""


class SessionCommandProvider(Provider):
    """Offers session-management commands (currently just `/clear`) via the command palette."""

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        score = max(matcher.match(CLEAR_SESSION_LABEL), matcher.match(CLEAR_SESSION_COMMAND))
        if score > 0:
            yield Hit(
                score, matcher.highlight(CLEAR_SESSION_LABEL), self._clear_session,
                help=CLEAR_SESSION_COMMAND)

    async def discover(self) -> Hits:
        yield DiscoveryHit(CLEAR_SESSION_LABEL, self._clear_session, help=CLEAR_SESSION_COMMAND)

    def _clear_session(self) -> None:
        cast(SupportsSessionClear, self.app).clear_session()
