# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.thinking_commands."""

from unittest.mock import MagicMock
from unittest.mock import PropertyMock
from unittest.mock import patch

from textual.widgets import OptionList
from textual.widgets import Static

from klorb.tui.thinking_commands import THINKING_EFFORT_HEADER_TEXT
from klorb.tui.thinking_commands import THINKING_EFFORT_LEVELS
from klorb.tui.thinking_commands import ThinkingCommandProvider
from klorb.tui.thinking_commands import ThinkingEffortScreen


def test_commands_lists_disable_and_set_effort_when_thinking_enabled() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_thinking_enabled.return_value = True
    provider = ThinkingCommandProvider(mock_screen)

    commands = provider._commands()

    assert "Enable thinking" not in commands
    assert "Disable thinking" in commands
    assert "Set thinking effort" in commands


def test_commands_lists_enable_and_set_effort_when_thinking_disabled() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_thinking_enabled.return_value = False
    provider = ThinkingCommandProvider(mock_screen)

    commands = provider._commands()

    assert "Enable thinking" in commands
    assert "Disable thinking" not in commands
    assert "Set thinking effort" in commands


def test_set_thinking_enabled_calls_app() -> None:
    mock_screen = MagicMock()
    provider = ThinkingCommandProvider(mock_screen)

    provider._set_thinking_enabled(True)

    mock_screen.app.set_thinking_enabled.assert_called_once_with(True)


def test_show_thinking_effort_screen_pushes_modal_with_current_effort() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_thinking_effort.return_value = "medium"
    provider = ThinkingCommandProvider(mock_screen)

    provider._show_thinking_effort_screen()

    (pushed_screen,), _ = mock_screen.app.push_screen.call_args
    assert isinstance(pushed_screen, ThinkingEffortScreen)
    assert pushed_screen._current_effort == "medium"


async def test_discover_yields_a_hit_per_command() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_thinking_enabled.return_value = True
    provider = ThinkingCommandProvider(mock_screen)

    hits = [hit async for hit in provider.discover()]

    labels = {str(hit.text) for hit in hits}
    assert "Enable thinking" not in labels
    assert "Disable thinking" in labels
    assert "Set thinking effort" in labels


async def test_search_filters_by_query() -> None:
    provider = ThinkingCommandProvider(MagicMock())

    hits = [hit async for hit in provider.search("effort")]
    no_hits = [hit async for hit in provider.search("not-a-real-command-xyz")]

    assert any("effort" in str(hit.text) for hit in hits)
    assert no_hits == []


def test_thinking_effort_screen_shows_header_and_marks_current_effort() -> None:
    screen = ThinkingEffortScreen("medium")

    container = next(iter(screen.compose()))
    header, option_list = container._pending_children

    assert isinstance(header, Static)
    assert str(header.render()) == THINKING_EFFORT_HEADER_TEXT
    assert isinstance(option_list, OptionList)
    prompts = tuple(str(option.prompt) for option in option_list._options)
    assert prompts == ("low", "medium *", "high")


def test_thinking_effort_screen_lists_all_levels_in_order() -> None:
    screen = ThinkingEffortScreen("low")

    container = next(iter(screen.compose()))
    _, option_list = container._pending_children

    assert isinstance(option_list, OptionList)
    levels = tuple(str(option.prompt).removesuffix(" *") for option in option_list._options)
    assert levels == THINKING_EFFORT_LEVELS


def test_on_option_list_option_selected_sets_effort_and_dismisses() -> None:
    screen = ThinkingEffortScreen("low")
    screen.dismiss = MagicMock()  # type: ignore[method-assign]
    event = MagicMock(option_index=1)
    mock_app = MagicMock()

    with patch.object(ThinkingEffortScreen, "app", new_callable=PropertyMock, return_value=mock_app):
        screen.on_option_list_option_selected(event)

    mock_app.set_thinking_effort.assert_called_once_with("medium")
    screen.dismiss.assert_called_once_with()
