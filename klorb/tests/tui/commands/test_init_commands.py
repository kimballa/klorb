# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.commands.init_commands."""

from unittest.mock import MagicMock, patch

from klorb.klorb_init import InitError
from klorb.tui.commands.init_commands import INIT_CONFIG_LABEL, InitCommandProvider


def test_run_init_calls_run_init_with_user_scope_and_shows_notice() -> None:
    mock_screen = MagicMock()
    provider = InitCommandProvider(mock_screen)

    with patch(
        "klorb.tui.commands.init_commands.run_init", return_value=["msg one", "msg two"],
    ) as mock_run_init:
        provider._run_init()

    mock_run_init.assert_called_once_with("user", force=False)
    mock_screen.app.show_notice.assert_called_once_with("msg one\nmsg two")


def test_run_init_shows_error_notice_on_init_error() -> None:
    mock_screen = MagicMock()
    provider = InitCommandProvider(mock_screen)

    with patch("klorb.tui.commands.init_commands.run_init", side_effect=InitError("boom")):
        provider._run_init()

    mock_screen.app.show_notice.assert_called_once_with("boom", error=True)


async def test_discover_yields_init_config_hit() -> None:
    with patch("klorb.tui.commands.init_commands.is_user_scope_initialized", return_value=False):
        provider = InitCommandProvider(MagicMock())

        hits = [hit async for hit in provider.discover()]

    assert any(hit.text == INIT_CONFIG_LABEL for hit in hits)


async def test_search_matches_label_query() -> None:
    with patch("klorb.tui.commands.init_commands.is_user_scope_initialized", return_value=False):
        provider = InitCommandProvider(MagicMock())

        hits = [hit async for hit in provider.search("init")]

    assert any(INIT_CONFIG_LABEL in str(hit.text) for hit in hits)


async def test_search_does_not_match_unrelated_query() -> None:
    with patch("klorb.tui.commands.init_commands.is_user_scope_initialized", return_value=False):
        provider = InitCommandProvider(MagicMock())

        hits = [hit async for hit in provider.search("not-a-real-command-xyz")]

    assert hits == []


async def test_discover_yields_nothing_when_user_scope_already_initialized() -> None:
    with patch("klorb.tui.commands.init_commands.is_user_scope_initialized", return_value=True):
        provider = InitCommandProvider(MagicMock())

        hits = [hit async for hit in provider.discover()]

    assert hits == []


async def test_search_yields_nothing_when_user_scope_already_initialized() -> None:
    with patch("klorb.tui.commands.init_commands.is_user_scope_initialized", return_value=True):
        provider = InitCommandProvider(MagicMock())

        hits = [hit async for hit in provider.search("init")]

    assert hits == []
