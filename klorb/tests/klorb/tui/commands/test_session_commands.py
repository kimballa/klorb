# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.commands.session_commands."""

from unittest.mock import MagicMock

from klorb.tui.commands.session_commands import SessionCommandProvider


def test_clear_session_calls_app_clear_session() -> None:
    mock_screen = MagicMock()
    provider = SessionCommandProvider(mock_screen)

    provider._clear_session()

    mock_screen.app.clear_session.assert_called_once_with()


async def test_discover_yields_clear_session_hit() -> None:
    provider = SessionCommandProvider(MagicMock())

    hits = [hit async for hit in provider.discover()]

    assert any(hit.text == "Clear session" for hit in hits)


async def test_search_matches_label_query() -> None:
    provider = SessionCommandProvider(MagicMock())

    hits = [hit async for hit in provider.search("clear")]

    assert any("Clear session" in str(hit.text) for hit in hits)


async def test_search_does_not_match_slash_command_query() -> None:
    """`/clear` is no longer a recognized alias — see `docs/specs/command-palette-from-prompt.md`
    ("Converting legacy commands") — so searching for it shouldn't surface `Clear session`
    (the `/` character doesn't appear in that label for the fuzzy matcher to find).
    """
    provider = SessionCommandProvider(MagicMock())

    hits = [hit async for hit in provider.search("/clear")]

    assert hits == []


async def test_search_does_not_match_unrelated_query() -> None:
    provider = SessionCommandProvider(MagicMock())

    hits = [hit async for hit in provider.search("not-a-real-command-xyz")]

    assert hits == []


async def test_discover_yields_rename_session_hit() -> None:
    provider = SessionCommandProvider(MagicMock())

    hits = [hit async for hit in provider.discover()]

    assert any(hit.text == "Rename session" for hit in hits)


async def test_search_matches_rename_query() -> None:
    provider = SessionCommandProvider(MagicMock())

    hits = [hit async for hit in provider.search("rename")]

    assert any("Rename session" in str(hit.text) for hit in hits)


def test_rename_session_opens_modal_with_current_title() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_current_session_title.return_value = "Old title"
    provider = SessionCommandProvider(mock_screen)

    provider._rename_session()

    mock_screen.app.push_screen.assert_called_once()
    screen_arg = mock_screen.app.push_screen.call_args[0][0]
    assert screen_arg._current_title == "Old title"
