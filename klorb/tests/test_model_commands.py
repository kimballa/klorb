# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.model_commands."""

from unittest.mock import MagicMock

from klorb.tui.model_commands import ModelCommandProvider


def test_commands_lists_discovered_models() -> None:
    provider = ModelCommandProvider(MagicMock())

    commands = provider._commands()

    assert commands["openai/gpt-4o-mini"] == "Select model: openai/gpt-4o-mini"


def test_select_model_calls_app_select_model() -> None:
    mock_screen = MagicMock()
    provider = ModelCommandProvider(mock_screen)

    provider._select_model("openai/gpt-4o-mini")

    mock_screen.app.select_model.assert_called_once_with("openai/gpt-4o-mini")


async def test_discover_yields_a_hit_per_model() -> None:
    provider = ModelCommandProvider(MagicMock())

    hits = [hit async for hit in provider.discover()]

    assert any(hit.text == "Select model: openai/gpt-4o-mini" for hit in hits)


async def test_search_filters_by_query() -> None:
    provider = ModelCommandProvider(MagicMock())

    hits = [hit async for hit in provider.search("gpt-4o-mini")]
    no_hits = [hit async for hit in provider.search("not-a-real-model-xyz")]

    assert any("gpt-4o-mini" in str(hit.text) for hit in hits)
    assert no_hits == []
