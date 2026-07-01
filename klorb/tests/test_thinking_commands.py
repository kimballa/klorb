# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.thinking_commands."""

from unittest.mock import MagicMock

from klorb.tui.thinking_commands import ThinkingCommandProvider


def test_commands_lists_enable_disable_and_effort_levels() -> None:
    provider = ThinkingCommandProvider(MagicMock())

    commands = provider._commands()

    assert "Enable thinking" in commands
    assert "Disable thinking" in commands
    assert "Thinking effort: low" in commands
    assert "Thinking effort: medium" in commands
    assert "Thinking effort: high" in commands


def test_set_thinking_enabled_calls_app() -> None:
    mock_screen = MagicMock()
    provider = ThinkingCommandProvider(mock_screen)

    provider._set_thinking_enabled(True)

    mock_screen.app.set_thinking_enabled.assert_called_once_with(True)


def test_set_thinking_effort_calls_app() -> None:
    mock_screen = MagicMock()
    provider = ThinkingCommandProvider(mock_screen)

    provider._set_thinking_effort("medium")

    mock_screen.app.set_thinking_effort.assert_called_once_with("medium")


async def test_discover_yields_a_hit_per_command() -> None:
    provider = ThinkingCommandProvider(MagicMock())

    hits = [hit async for hit in provider.discover()]

    labels = {str(hit.text) for hit in hits}
    assert "Enable thinking" in labels
    assert "Disable thinking" in labels
    assert "Thinking effort: high" in labels


async def test_search_filters_by_query() -> None:
    provider = ThinkingCommandProvider(MagicMock())

    hits = [hit async for hit in provider.search("effort")]
    no_hits = [hit async for hit in provider.search("not-a-real-command-xyz")]

    assert any("effort" in str(hit.text) for hit in hits)
    assert no_hits == []
