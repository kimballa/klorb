# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.commands.thinking_commands."""

from unittest.mock import MagicMock, PropertyMock, patch

from textual.command import DiscoveryHit
from textual.widgets import OptionList, Static

from klorb.tui.commands.thinking_commands import (
    THINKING_EFFORT_HEADER_TEXT,
    THINKING_EFFORT_LEVELS,
    ThinkingCommandProvider,
    ThinkingEffortScreen,
)


def test_commands_lists_disable_and_set_effort_when_thinking_enabled() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_thinking_enabled.return_value = True
    mock_screen.app.get_thinking_effort.return_value = "high"
    provider = ThinkingCommandProvider(mock_screen)

    commands = provider._commands()

    assert "Enable thinking" not in commands
    assert "Disable thinking" in commands
    assert "Set thinking effort (High)" in commands


def test_commands_lists_enable_and_set_effort_when_thinking_disabled() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_thinking_enabled.return_value = False
    mock_screen.app.get_thinking_effort.return_value = "low"
    provider = ThinkingCommandProvider(mock_screen)

    commands = provider._commands()

    assert "Enable thinking" in commands
    assert "Disable thinking" not in commands
    assert "Set thinking effort (Low)" in commands


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
    mock_screen.app.get_thinking_effort.return_value = "medium"
    provider = ThinkingCommandProvider(mock_screen)

    hits = [hit async for hit in provider.discover()]
    assert all(isinstance(hit, DiscoveryHit) for hit in hits)

    displayed = {str(hit.display) for hit in hits if isinstance(hit, DiscoveryHit)}
    assert "Enable thinking" not in displayed
    assert "Disable thinking" in displayed
    assert "Set thinking effort (Medium)" in displayed


async def test_discover_canonicalizes_toggle_text_to_its_own_display() -> None:
    """A toggle command's canonical `text` (used to recall it later, see
    `docs/specs/command-palette-from-prompt.md`) is exactly what's displayed, since
    "Disable thinking" is itself a stable, non-dynamic label.
    """
    mock_screen = MagicMock()
    mock_screen.app.get_thinking_enabled.return_value = True
    mock_screen.app.get_thinking_effort.return_value = "medium"
    provider = ThinkingCommandProvider(mock_screen)

    hits = [hit async for hit in provider.discover()]

    disable_hit = next(
        hit for hit in hits if isinstance(hit, DiscoveryHit) and hit.display == "Disable thinking")
    assert disable_hit.text == "Disable thinking"


async def test_discover_canonicalizes_set_effort_text_to_its_undecorated_root() -> None:
    """`Set thinking effort (Medium)`'s parenthetical suffix changes with the current
    effort level, so its canonical `text` is always the undecorated root name — recording
    the decorated display text verbatim wouldn't reliably match this same command again.
    """
    mock_screen = MagicMock()
    mock_screen.app.get_thinking_enabled.return_value = True
    mock_screen.app.get_thinking_effort.return_value = "medium"
    provider = ThinkingCommandProvider(mock_screen)

    hits = [hit async for hit in provider.discover()]

    effort_hit = next(
        hit for hit in hits
        if isinstance(hit, DiscoveryHit) and hit.display == "Set thinking effort (Medium)")
    assert effort_hit.text == "Set thinking effort"


async def test_search_filters_by_query() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_thinking_enabled.return_value = True
    mock_screen.app.get_thinking_effort.return_value = "high"
    provider = ThinkingCommandProvider(mock_screen)

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
