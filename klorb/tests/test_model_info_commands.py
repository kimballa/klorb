# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.model_info_commands."""

from unittest.mock import MagicMock, patch

from textual.command import DiscoveryHit

from klorb.models.configured_model import ConfiguredModel
from klorb.models.openrouter_pricing import ModelPricing
from klorb.tui.model_info_commands import (
    MODEL_INFO_HEADER_TEXT,
    NOT_AVAILABLE,
    SHOW_MODEL_INFO_LABEL,
    ModelInfoCommandProvider,
    ModelInfoScreen,
    format_model_info,
)


def _model(data: dict) -> ConfiguredModel:
    return ConfiguredModel(data, source="test")


def test_format_model_info_includes_name_family_and_version() -> None:
    model = _model({"name": "anthropic/claude-sonnet-5", "family": "claude-sonnet", "model_version": "5.0"})

    info = format_model_info(model, None)

    assert "Name: anthropic/claude-sonnet-5" in info
    assert "Family: claude-sonnet" in info
    assert "Version: 5.0" in info


def test_format_model_info_reports_not_available_for_unknown_family_version_and_pricing() -> None:
    model = _model({"name": "some/model"})

    info = format_model_info(model, None)

    assert f"Family: {NOT_AVAILABLE}" in info
    assert f"Version: {NOT_AVAILABLE}" in info
    assert f"Cost per MTok (in / out): {NOT_AVAILABLE}" in info
    assert f"Klorb capabilities: {NOT_AVAILABLE}" in info


def test_format_model_info_renders_capabilities_in_order_with_thinking_budget_style() -> None:
    model = _model({
        "name": "some/model",
        "capabilities": {
            "vision": True,
            "thinking": True,
            "thinking_budget_style": "tokens",
            "max_context_window": 400_000,
            "max_output_tokens": 128_000,
            "function_calling": True,
            "streaming": False,
        },
    })

    info = format_model_info(model, None)
    lines = info.splitlines()

    assert "Vision: Yes" in lines
    assert "Thinking: Yes (tokens)" in lines
    assert "Max context window: 400,000 tokens" in lines
    assert "Max output tokens: 128,000 tokens" in lines
    assert "Function calling: Yes" in lines
    assert "Streaming: No" in lines
    assert lines.index("Vision: Yes") < lines.index("Thinking: Yes (tokens)")


def test_format_model_info_shows_provider_specific_capability_keys() -> None:
    model = _model({"name": "some/model", "capabilities": {"custom_flag": True}})

    assert "custom_flag: Yes" in format_model_info(model, None)


def test_format_model_info_shows_klorb_capabilities() -> None:
    model = _model({
        "name": "openai/gpt-oss-safeguard-20b:nitro",
        "klorb_capabilities": {"BASH_SAFETY_EVAL": True},
    })

    assert "Klorb capabilities: BASH_SAFETY_EVAL=Yes" in format_model_info(model, None)


def test_format_model_info_includes_pricing_when_given() -> None:
    model = _model({"name": "some/model"})
    pricing = ModelPricing(input_cost_per_mtok=0.11, output_cost_per_mtok=0.8)

    assert "Cost per MTok (in / out): 0.11 / 0.8 USD" in format_model_info(model, pricing)


def test_model_info_screen_shows_header_and_formatted_body() -> None:
    model = _model({"name": "some/model"})
    screen = ModelInfoScreen(model, None)

    container = next(iter(screen.compose()))
    header, body = container._pending_children

    assert str(header.render()) == MODEL_INFO_HEADER_TEXT
    assert str(body.render()) == format_model_info(model, None)


async def test_show_model_info_screen_pushes_modal_with_fetched_pricing() -> None:
    mock_screen = MagicMock()
    model = _model({"name": "some/model"})
    mock_screen.app.get_active_model.return_value = model
    pricing = ModelPricing(input_cost_per_mtok=0.11, output_cost_per_mtok=0.8)
    provider = ModelInfoCommandProvider(mock_screen)

    with patch(
        "klorb.tui.model_info_commands.fetch_openrouter_pricing", return_value=pricing,
    ) as mock_fetch:
        await provider._show_model_info_screen()

    mock_fetch.assert_called_once_with("some/model")
    (pushed_screen,), _ = mock_screen.app.push_screen.call_args
    assert isinstance(pushed_screen, ModelInfoScreen)
    assert pushed_screen._model is model
    assert pushed_screen._pricing is pricing


async def test_show_model_info_screen_reports_a_notice_when_no_model_is_active() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_active_model.return_value = None
    mock_screen.app.show_notice = MagicMock()
    provider = ModelInfoCommandProvider(mock_screen)

    await provider._show_model_info_screen()

    mock_screen.app.push_screen.assert_not_called()
    mock_screen.app.show_notice.assert_called_once()


async def test_discover_yields_a_single_hit() -> None:
    mock_screen = MagicMock()
    provider = ModelInfoCommandProvider(mock_screen)

    hits = [hit async for hit in provider.discover()]

    assert len(hits) == 1
    hit = hits[0]
    assert isinstance(hit, DiscoveryHit)
    assert str(hit.display) == SHOW_MODEL_INFO_LABEL


async def test_search_filters_by_query() -> None:
    mock_screen = MagicMock()
    provider = ModelInfoCommandProvider(mock_screen)

    hits = [hit async for hit in provider.search("model info")]
    no_hits = [hit async for hit in provider.search("not-a-real-command-xyz")]

    assert len(hits) == 1
    assert no_hits == []
