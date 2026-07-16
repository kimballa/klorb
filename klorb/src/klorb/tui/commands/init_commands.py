# © Copyright 2026 Aaron Kimball
"""Command palette provider offering "Init local klorb config", the interactive equivalent of
`klorb init --user` — see docs/specs/klorb-init.md.
"""

from typing import Protocol, cast

from textual.command import DiscoveryHit, Hit, Hits, Provider

from klorb.klorb_init import InitError, is_user_scope_initialized, run_init

INIT_CONFIG_LABEL = "Init local klorb config"


class SupportsShowNotice(Protocol):
    """Structural interface for an App that can append a status message to its history."""

    def show_notice(self, message: str, *, error: bool = False) -> None:
        """Show `message` to the user, e.g. as a `.notice`/`.error` item in the history scroll."""


class InitCommandProvider(Provider):
    """Offers "Init local klorb config" via the command palette (`ctrl+p` or by typing
    `>init` in the prompt) — runs `klorb.klorb_init.run_init("user", force=False)` for the
    user running this REPL, then reports the outcome via `App.show_notice()`. Yields no
    hits at all when `klorb.klorb_init.is_user_scope_initialized()` is `True` (both the
    user-scope config file and the `klorb` symlink already exist), since the command would
    be a pure no-op in that case — a hidden command rather than a confusing "already
    exists" notice.
    """

    async def search(self, query: str) -> Hits:
        if is_user_scope_initialized():
            return
        matcher = self.matcher(query)
        score = matcher.match(INIT_CONFIG_LABEL)
        if score > 0:
            yield Hit(score, matcher.highlight(INIT_CONFIG_LABEL), self._run_init)

    async def discover(self) -> Hits:
        if is_user_scope_initialized():
            return
        yield DiscoveryHit(INIT_CONFIG_LABEL, self._run_init)

    def _run_init(self) -> None:
        app = cast(SupportsShowNotice, self.app)
        try:
            messages = run_init("user", force=False)
        except InitError as exc:
            app.show_notice(str(exc), error=True)
            return
        app.show_notice("\n".join(messages))
