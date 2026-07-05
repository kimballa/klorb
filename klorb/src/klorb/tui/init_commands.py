# © Copyright 2026 Aaron Kimball
"""Command palette provider offering "Init local klorb config", the interactive equivalent of
`klorb init --user` — see docs/specs/klorb-init.md.
"""

from typing import Protocol
from typing import cast

from textual.command import DiscoveryHit
from textual.command import Hit
from textual.command import Hits
from textual.command import Provider

from klorb.klorb_init import InitError
from klorb.klorb_init import run_init

INIT_CONFIG_LABEL = "Init local klorb config"


class SupportsNotify(Protocol):
    """Structural interface for an App that can show a toast notification."""

    def notify(self, message: str, *, severity: str = "information") -> None:
        """Show `message` to the user, e.g. in a toast."""


class InitCommandProvider(Provider):
    """Offers "Init local klorb config" via the command palette (`ctrl+p` or by typing
    `>init` in the prompt) — runs `klorb.klorb_init.run_init("user", force=False)` for the
    user running this REPL, then reports the outcome via `App.notify()`.
    """

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        score = matcher.match(INIT_CONFIG_LABEL)
        if score > 0:
            yield Hit(score, matcher.highlight(INIT_CONFIG_LABEL), self._run_init)

    async def discover(self) -> Hits:
        yield DiscoveryHit(INIT_CONFIG_LABEL, self._run_init)

    def _run_init(self) -> None:
        app = cast(SupportsNotify, self.app)
        try:
            messages = run_init("user", force=False)
        except InitError as exc:
            app.notify(str(exc), severity="error")
            return
        app.notify("\n".join(messages))
