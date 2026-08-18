# © Copyright 2026 Aaron Kimball
"""Command palette provider offering "Trust workspace". See docs/specs/projects-and-trust.md."""

from typing import Protocol, cast

from textual.command import DiscoveryHit, Hit, Hits, Provider

TRUST_WORKSPACE_LABEL = "Trust workspace"


class SupportsTrustWorkspace(Protocol):
    """Structural interface for an App that can report and act on workspace trust state."""

    def workspace_trust_management_enabled(self) -> bool:
        """Whether this app was given a TrustManager at all."""

    def is_workspace_trusted(self) -> bool:
        """Whether the current workspace is currently trusted."""

    def trust_workspace(self) -> None:
        """Confirm with the user, then trust the current workspace."""


class TrustWorkspaceCommandProvider(Provider):
    """Offers "Trust workspace" via the command palette."""

    async def search(self, query: str) -> Hits:
        app = cast(SupportsTrustWorkspace, self.app)
        if not self._visible(app):
            return
        matcher = self.matcher(query)
        score = matcher.match(TRUST_WORKSPACE_LABEL)
        if score > 0:
            yield Hit(score, matcher.highlight(TRUST_WORKSPACE_LABEL), self._trust_workspace)

    async def discover(self) -> Hits:
        app = cast(SupportsTrustWorkspace, self.app)
        if not self._visible(app):
            return
        yield DiscoveryHit(TRUST_WORKSPACE_LABEL, self._trust_workspace)

    def _visible(self, app: SupportsTrustWorkspace) -> bool:
        return app.workspace_trust_management_enabled() and not app.is_workspace_trusted()

    def _trust_workspace(self) -> None:
        cast(SupportsTrustWorkspace, self.app).trust_workspace()
