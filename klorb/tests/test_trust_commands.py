# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.trust_commands."""

from unittest.mock import MagicMock

from klorb.tui.trust_commands import TRUST_WORKSPACE_LABEL, TrustWorkspaceCommandProvider


def _screen(*, trust_management_enabled: bool, workspace_trusted: bool) -> MagicMock:
    screen = MagicMock()
    screen.app.workspace_trust_management_enabled.return_value = trust_management_enabled
    screen.app.is_workspace_trusted.return_value = workspace_trusted
    return screen


async def test_discover_yields_a_hit_when_untrusted_and_management_enabled() -> None:
    provider = TrustWorkspaceCommandProvider(
        _screen(trust_management_enabled=True, workspace_trusted=False))

    hits = [hit async for hit in provider.discover()]

    assert any(hit.text == TRUST_WORKSPACE_LABEL for hit in hits)


async def test_discover_yields_nothing_when_already_trusted() -> None:
    provider = TrustWorkspaceCommandProvider(
        _screen(trust_management_enabled=True, workspace_trusted=True))

    hits = [hit async for hit in provider.discover()]

    assert hits == []


async def test_discover_yields_nothing_when_trust_management_disabled() -> None:
    provider = TrustWorkspaceCommandProvider(
        _screen(trust_management_enabled=False, workspace_trusted=False))

    hits = [hit async for hit in provider.discover()]

    assert hits == []


async def test_search_matches_label_query_when_visible() -> None:
    provider = TrustWorkspaceCommandProvider(
        _screen(trust_management_enabled=True, workspace_trusted=False))

    hits = [hit async for hit in provider.search("trust")]

    assert any(TRUST_WORKSPACE_LABEL in str(hit.text) for hit in hits)


async def test_search_yields_nothing_when_already_trusted() -> None:
    provider = TrustWorkspaceCommandProvider(
        _screen(trust_management_enabled=True, workspace_trusted=True))

    hits = [hit async for hit in provider.search("trust")]

    assert hits == []


async def test_selecting_the_hit_calls_trust_workspace_on_the_app() -> None:
    screen = _screen(trust_management_enabled=True, workspace_trusted=False)
    provider = TrustWorkspaceCommandProvider(screen)

    hits = [hit async for hit in provider.discover()]
    assert len(hits) == 1
    hits[0].command()

    screen.app.trust_workspace.assert_called_once_with()
