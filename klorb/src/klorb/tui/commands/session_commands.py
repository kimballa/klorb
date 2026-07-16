# © Copyright 2026 Aaron Kimball
"""Command palette provider that lets the user manage the active session — clearing it
or inspecting its run-time statistics."""

from typing import Protocol, cast

from textual.command import DiscoveryHit, Hit, Hits, Provider

from klorb.session_statistics import SessionStatistics

CLEAR_SESSION_LABEL = "Clear session"
SHOW_SESSION_STATS_LABEL = "Show session stats"


class SupportsSessionClear(Protocol):
    """Structural interface for an App that can clear its active session."""

    def clear_session(self) -> None:
        """Discard the active session's history and start a fresh one."""
        ...


class SupportsSessionStats(Protocol):
    """Structural interface for an App that can report its session statistics."""

    def get_session_statistics(self) -> SessionStatistics:
        """Return the active session's running statistics."""
        ...

    def show_notice(self, message: str, *, error: bool = False) -> None:
        """Report a one-off status/result in the history scroll."""
        ...


class SessionCommandProvider(Provider):
    """Offers session-management commands (clearing the active session, showing
    session statistics) via the command palette — reachable via ``ctrl+p`` or by typing
    ``>clear`` / ``>show session stats`` in the prompt (see
    ``docs/specs/command-palette-from-prompt.md``).
    """

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        score = matcher.match(CLEAR_SESSION_LABEL)
        if score > 0:
            yield Hit(score, matcher.highlight(CLEAR_SESSION_LABEL), self._clear_session)
        score = matcher.match(SHOW_SESSION_STATS_LABEL)
        if score > 0:
            yield Hit(score, matcher.highlight(SHOW_SESSION_STATS_LABEL), self._show_session_stats)

    async def discover(self) -> Hits:
        yield DiscoveryHit(CLEAR_SESSION_LABEL, self._clear_session)
        yield DiscoveryHit(SHOW_SESSION_STATS_LABEL, self._show_session_stats)

    def _clear_session(self) -> None:
        cast(SupportsSessionClear, self.app).clear_session()

    def _show_session_stats(self) -> None:
        app = cast(SupportsSessionStats, self.app)
        stats = app.get_session_statistics()
        app.show_notice(stats.format_report())
