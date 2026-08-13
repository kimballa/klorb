# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.commands.thinking_commands."""

from unittest.mock import MagicMock, PropertyMock, patch

from textual.command import DiscoveryHit
from textual.widgets import OptionList, Static

from klorb.tui.commands.thinking_commands import (
    THINKING_STATE_HEADER_TEXT,
    THINKING_STATE_LABEL,
    THINKING_STATE_OPTIONS,
    ThinkingCommandProvider,
    ThinkingStateScreen,
)


def test_command_shows_set_thinking_off_when_disabled() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_thinking_enabled.return_value = False
    mock_screen.app.get_thinking_effort.return_value = "high"
    provider = ThinkingCommandProvider(mock_screen)

    label, _ = provider._command()

    assert label == "Set thinking (Off)"


def test_command_shows_set_thinking_effort_when_enabled() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_thinking_enabled.return_value = True
    mock_screen.app.get_thinking_effort.return_value = "medium"
    provider = ThinkingCommandProvider(mock_screen)

    label, _ = provider._command()

    assert label == "Set thinking (Medium)"


def test_show_thinking_state_screen_pushes_modal_with_current_state() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_thinking_enabled.return_value = True
    mock_screen.app.get_thinking_effort.return_value = "medium"
    provider = ThinkingCommandProvider(mock_screen)

    provider._show_thinking_state_screen()

    (pushed_screen,), _ = mock_screen.app.push_screen.call_args
    assert isinstance(pushed_screen, ThinkingStateScreen)
    assert pushed_screen._thinking_enabled is True
    assert pushed_screen._current_effort == "medium"


async def test_discover_yields_single_hit() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_thinking_enabled.return_value = True
    mock_screen.app.get_thinking_effort.return_value = "medium"
    provider = ThinkingCommandProvider(mock_screen)

    hits = [hit async for hit in provider.discover()]
    assert len(hits) == 1
    assert isinstance(hits[0], DiscoveryHit)


async def test_discover_canonicalizes_text_to_set_thinking() -> None:
    """The canonical `text` (used for recall, see
    `docs/specs/command-palette-from-prompt.md`) is always the undecorated label,
    since the display text's parenthetical suffix changes with the current state.
    """
    mock_screen = MagicMock()
    mock_screen.app.get_thinking_enabled.return_value = True
    mock_screen.app.get_thinking_effort.return_value = "medium"
    provider = ThinkingCommandProvider(mock_screen)

    hits = [hit async for hit in provider.discover()]
    hit = hits[0]

    assert str(hit.display) == "Set thinking (Medium)"  # type: ignore[union-attr]
    assert hit.text == THINKING_STATE_LABEL


async def test_search_filters_by_query() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_thinking_enabled.return_value = True
    mock_screen.app.get_thinking_effort.return_value = "high"
    provider = ThinkingCommandProvider(mock_screen)

    hits = [hit async for hit in provider.search("thinking")]
    no_hits = [hit async for hit in provider.search("not-a-real-command-xyz")]

    assert any("thinking" in str(hit.text) for hit in hits)
    assert no_hits == []


def test_thinking_state_screen_shows_header_and_marks_current_state() -> None:
    screen = ThinkingStateScreen(True, "medium")

    container = next(iter(screen.compose()))
    header, option_list = container._pending_children

    assert isinstance(header, Static)
    assert str(header.render()) == THINKING_STATE_HEADER_TEXT
    assert isinstance(option_list, OptionList)
    prompts = tuple(str(option.prompt) for option in option_list._options)  # type: ignore[attr-defined]
    assert prompts == ("off", "low", "medium *", "high")


def test_thinking_state_screen_marks_off_when_disabled() -> None:
    screen = ThinkingStateScreen(False, "high")

    container = next(iter(screen.compose()))
    _, option_list = container._pending_children

    prompts = tuple(str(option.prompt) for option in option_list._options)  # type: ignore[attr-defined]
    assert prompts == ("off *", "low", "medium", "high")


def test_thinking_state_screen_lists_all_states_in_order() -> None:
    screen = ThinkingStateScreen(False, "low")

    container = next(iter(screen.compose()))
    _, option_list = container._pending_children

    assert isinstance(option_list, OptionList)
    states = tuple(str(option.prompt).removesuffix(" *")
                   for option in option_list._options)  # type: ignore[attr-defined]
    assert states == THINKING_STATE_OPTIONS


def test_on_option_list_option_selected_off_disables_thinking() -> None:
    screen = ThinkingStateScreen(True, "medium")
    screen.dismiss = MagicMock()  # type: ignore[method-assign]
    event = MagicMock(option_index=0)
    mock_app = MagicMock()

    with patch.object(ThinkingStateScreen, "app", new_callable=PropertyMock, return_value=mock_app):
        screen.on_option_list_option_selected(event)

    mock_app.set_thinking_enabled.assert_called_once_with(False)
    mock_app.set_thinking_effort.assert_not_called()
    screen.dismiss.assert_called_once_with()


def test_on_option_list_option_selected_effort_enables_and_sets_effort() -> None:
    screen = ThinkingStateScreen(False, "low")
    screen.dismiss = MagicMock()  # type: ignore[method-assign]
    event = MagicMock(option_index=2)  # "medium"
    mock_app = MagicMock()

    with patch.object(ThinkingStateScreen, "app", new_callable=PropertyMock, return_value=mock_app):
        screen.on_option_list_option_selected(event)

    mock_app.set_thinking_enabled.assert_called_once_with(True)
    mock_app.set_thinking_effort.assert_called_once_with("medium")
    screen.dismiss.assert_called_once_with()
