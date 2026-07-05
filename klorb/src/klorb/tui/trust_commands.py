# © Copyright 2026 Aaron Kimball
"""Command palette provider offering "Trust workspace" — the interactive equivalent of the
"Do you trust this workspace?" bootstrap prompt, reachable any time later for a workspace the
user (or a previous session) left untrusted. See docs/specs/projects-and-trust.md.
"""

from typing import Protocol
from typing import cast

from textual.command import DiscoveryHit
from textual.command import Hit
from textual.command import Hits
from textual.command import Provider

TRUST_WORKSPACE_LABEL = "Trust workspace"


class SupportsTrustWorkspace(Protocol):
    """Structural interface for an App that can report and act on workspace trust state."""

    def workspace_trust_management_enabled(self) -> bool:
        """Whether this app was given a `TrustManager` at all — workspace trust management is
        entirely opt-in (see `klorb.tui.repl.ReplApp`), so this command stays hidden rather
        than acting on a workspace nobody asked to track."""

    def is_workspace_trusted(self) -> bool:
        """Whether the current workspace is currently trusted."""

    def trust_workspace(self) -> None:
        """Confirm with the user, then trust the current workspace.

        On the real `klorb.tui.repl.ReplApp`, this is a `@work()`-decorated method: calling it
        starts a Textual worker and returns immediately (the worker itself is what awaits the
        user's confirmation), rather than a coroutine this Protocol's caller need await."""


class TrustWorkspaceCommandProvider(Provider):
    """Offers "Trust workspace" via the command palette (`ctrl+p` or by typing `>trust` in the
    prompt) — but only while trust management is enabled and the current workspace isn't
    already trusted; a trusted (or trust-management-disabled) app yields no hits at all, so the
    command simply doesn't show up rather than being offered as a confusing no-op.
    """

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
