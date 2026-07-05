# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.init_commands."""

from unittest.mock import MagicMock
from unittest.mock import patch

from klorb.klorb_init import InitError
from klorb.tui.init_commands import INIT_CONFIG_LABEL
from klorb.tui.init_commands import InitCommandProvider


def test_run_init_calls_run_init_with_user_scope_and_notifies_messages() -> None:
    mock_screen = MagicMock()
    provider = InitCommandProvider(mock_screen)

    with patch("klorb.tui.init_commands.run_init", return_value=["msg one", "msg two"]) as mock_run_init:
        provider._run_init()

    mock_run_init.assert_called_once_with("user", force=False)
    mock_screen.app.notify.assert_called_once_with("msg one\nmsg two")


def test_run_init_notifies_error_severity_on_init_error() -> None:
    mock_screen = MagicMock()
    provider = InitCommandProvider(mock_screen)

    with patch("klorb.tui.init_commands.run_init", side_effect=InitError("boom")):
        provider._run_init()

    mock_screen.app.notify.assert_called_once_with("boom", severity="error")


async def test_discover_yields_init_config_hit() -> None:
    provider = InitCommandProvider(MagicMock())

    hits = [hit async for hit in provider.discover()]

    assert any(hit.text == INIT_CONFIG_LABEL for hit in hits)


async def test_search_matches_label_query() -> None:
    provider = InitCommandProvider(MagicMock())

    hits = [hit async for hit in provider.search("init")]

    assert any(INIT_CONFIG_LABEL in str(hit.text) for hit in hits)


async def test_search_does_not_match_unrelated_query() -> None:
    provider = InitCommandProvider(MagicMock())

    hits = [hit async for hit in provider.search("not-a-real-command-xyz")]

    assert hits == []
