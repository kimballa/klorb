# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.commands.model_commands."""

from unittest.mock import MagicMock, PropertyMock, patch

from textual.command import DiscoveryHit
from textual.widgets import Input, Static

from klorb.tui.commands.model_commands import (
    CHANGE_MODEL_LABEL,
    CURRENT_MODEL_MARKER,
    MODEL_HEADER_TEXT,
    ModelCommandProvider,
    ModelSelectionScreen,
    _ModelOptionList,
    filter_model_names,
)


def test_filter_model_names_returns_alphabetical_order_for_empty_query() -> None:
    names = ["z-ai/glm-5.2", "openai/gpt-5-nano", "anthropic/claude-sonnet-5"]

    assert filter_model_names(names, "") == [
        "anthropic/claude-sonnet-5", "openai/gpt-5-nano", "z-ai/glm-5.2"]


def test_filter_model_names_ranks_and_excludes_non_matches() -> None:
    names = ["qwen/qwen3-coder-next", "moonshotai/kimi-k2.7-code", "openai/gpt-5-nano"]

    matches = filter_model_names(names, "qwen")

    assert matches == ["qwen/qwen3-coder-next"]


def test_filter_model_names_excludes_everything_for_no_match() -> None:
    names = ["openai/gpt-5-nano", "z-ai/glm-5.2"]

    assert filter_model_names(names, "not-a-real-model-xyz") == []


def test_model_selection_screen_shows_header_input_and_option_list() -> None:
    screen = ModelSelectionScreen(["alpha", "beta"], "beta")

    container = next(iter(screen.compose()))
    header, filter_input, option_list = container._pending_children

    assert isinstance(header, Static)
    assert str(header.render()) == MODEL_HEADER_TEXT
    assert isinstance(filter_input, Input)
    assert isinstance(option_list, _ModelOptionList)


def test_render_option_marks_the_current_model() -> None:
    from textual.content import Content

    screen = ModelSelectionScreen(["alpha", "beta"], "beta")

    other_option = screen._render_option("alpha", Content("alpha"))
    current_option = screen._render_option("beta", Content("beta"))

    assert str(other_option.prompt) == "alpha"
    assert str(current_option.prompt) == f"beta{CURRENT_MODEL_MARKER}"


def test_select_calls_app_select_model_and_dismisses() -> None:
    screen = ModelSelectionScreen(["alpha", "beta"], "alpha")
    screen.dismiss = MagicMock()  # type: ignore[method-assign]
    mock_app = MagicMock()

    with patch.object(ModelSelectionScreen, "app", new_callable=PropertyMock, return_value=mock_app):
        screen._select("beta")

    mock_app.select_model.assert_called_once_with("beta")
    screen.dismiss.assert_called_once_with()


def test_on_option_list_option_selected_selects_the_filtered_name_at_that_index() -> None:
    screen = ModelSelectionScreen(["alpha", "beta", "gamma"], "alpha")
    screen._filtered_names = ["beta", "gamma"]
    screen._select = MagicMock()  # type: ignore[method-assign]
    event = MagicMock(option_index=1)

    screen.on_option_list_option_selected(event)

    screen._select.assert_called_once_with("gamma")


def test_label_includes_current_model_name() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_current_model_name.return_value = "openai/gpt-5-nano"
    provider = ModelCommandProvider(mock_screen)

    assert provider._label() == f"{CHANGE_MODEL_LABEL} (openai/gpt-5-nano)"


def test_show_model_screen_pushes_modal_with_available_models_and_current() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_current_model_name.return_value = "openai/gpt-5-nano"
    mock_screen.app.available_model_names.return_value = ["openai/gpt-5-nano", "z-ai/glm-5.2"]
    provider = ModelCommandProvider(mock_screen)

    provider._show_model_screen()

    (pushed_screen,), _ = mock_screen.app.push_screen.call_args
    assert isinstance(pushed_screen, ModelSelectionScreen)
    assert pushed_screen._model_names == ["openai/gpt-5-nano", "z-ai/glm-5.2"]
    assert pushed_screen._current_model_name == "openai/gpt-5-nano"


async def test_discover_yields_a_single_hit_with_undecorated_canonical_text() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_current_model_name.return_value = "openai/gpt-5-nano"
    provider = ModelCommandProvider(mock_screen)

    hits = [hit async for hit in provider.discover()]

    assert len(hits) == 1
    hit = hits[0]
    assert isinstance(hit, DiscoveryHit)
    assert str(hit.display) == f"{CHANGE_MODEL_LABEL} (openai/gpt-5-nano)"
    assert hit.text == CHANGE_MODEL_LABEL


async def test_search_filters_by_query() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_current_model_name.return_value = "openai/gpt-5-nano"
    provider = ModelCommandProvider(mock_screen)

    hits = [hit async for hit in provider.search("model")]
    no_hits = [hit async for hit in provider.search("not-a-real-command-xyz")]

    assert any("model" in str(hit.text).lower() for hit in hits)
    assert no_hits == []
