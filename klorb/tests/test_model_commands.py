# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.model_commands."""

from unittest.mock import MagicMock

from klorb.tui.model_commands import ModelCommandProvider


def test_commands_lists_discovered_models() -> None:
    provider = ModelCommandProvider(MagicMock())

    commands = provider._commands()

    assert commands["openai/gpt-5-nano"] == "Select model: openai/gpt-5-nano"


def test_select_model_calls_app_select_model() -> None:
    mock_screen = MagicMock()
    provider = ModelCommandProvider(mock_screen)

    provider._select_model("openai/gpt-5-nano")

    mock_screen.app.select_model.assert_called_once_with("openai/gpt-5-nano")


async def test_discover_yields_a_hit_per_model() -> None:
    provider = ModelCommandProvider(MagicMock())

    hits = [hit async for hit in provider.discover()]

    assert any(hit.text == "Select model: openai/gpt-5-nano" for hit in hits)


async def test_search_filters_by_query() -> None:
    provider = ModelCommandProvider(MagicMock())

    hits = [hit async for hit in provider.search("gpt-5-nano")]
    no_hits = [hit async for hit in provider.search("not-a-real-model-xyz")]

    assert any("gpt-5-nano" in str(hit.text) for hit in hits)
    assert no_hits == []
