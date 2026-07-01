# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.session_commands."""

from unittest.mock import MagicMock

from klorb.tui.session_commands import SessionCommandProvider


def test_clear_session_calls_app_clear_session() -> None:
    mock_screen = MagicMock()
    provider = SessionCommandProvider(mock_screen)

    provider._clear_session()

    mock_screen.app.clear_session.assert_called_once_with()


async def test_discover_yields_clear_session_hit() -> None:
    provider = SessionCommandProvider(MagicMock())

    hits = [hit async for hit in provider.discover()]

    assert any(hit.text == "Clear session" and hit.help == "/clear" for hit in hits)


async def test_search_matches_label_query() -> None:
    provider = SessionCommandProvider(MagicMock())

    hits = [hit async for hit in provider.search("clear")]

    assert any("Clear session" in str(hit.text) for hit in hits)


async def test_search_matches_slash_command_query() -> None:
    provider = SessionCommandProvider(MagicMock())

    hits = [hit async for hit in provider.search("/clear")]

    assert any("Clear session" in str(hit.text) for hit in hits)


async def test_search_does_not_match_unrelated_query() -> None:
    provider = SessionCommandProvider(MagicMock())

    hits = [hit async for hit in provider.search("not-a-real-command-xyz")]

    assert hits == []
