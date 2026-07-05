# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.theme_commands."""

from unittest.mock import MagicMock
from unittest.mock import PropertyMock
from unittest.mock import patch

from textual.command import DiscoveryHit
from textual.widgets import OptionList
from textual.widgets import Static

from klorb.tui.theme_commands import SELECT_THEME_LABEL
from klorb.tui.theme_commands import THEME_HEADER_TEXT
from klorb.tui.theme_commands import ThemeCommandProvider
from klorb.tui.theme_commands import ThemeSelectionScreen


def test_theme_selection_screen_shows_header_and_marks_current_theme() -> None:
    screen = ThemeSelectionScreen(["dracula", "nord", "solarized-light"], "nord")

    container = next(iter(screen.compose()))
    header, option_list = container._pending_children

    assert isinstance(header, Static)
    assert str(header.render()) == THEME_HEADER_TEXT
    assert isinstance(option_list, OptionList)
    prompts = tuple(str(option.prompt) for option in option_list._options)
    assert prompts == ("dracula", "nord (*)", "solarized-light")


def test_on_option_list_option_selected_selects_theme_and_dismisses() -> None:
    screen = ThemeSelectionScreen(["dracula", "nord"], "dracula")
    screen.dismiss = MagicMock()  # type: ignore[method-assign]
    event = MagicMock(option_index=1)
    mock_app = MagicMock()

    with patch.object(ThemeSelectionScreen, "app", new_callable=PropertyMock, return_value=mock_app):
        screen.on_option_list_option_selected(event)

    mock_app.select_theme.assert_called_once_with("nord")
    screen.dismiss.assert_called_once_with()


def test_label_includes_current_theme_name() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_current_theme_name.return_value = "nord"
    provider = ThemeCommandProvider(mock_screen)

    assert provider._label() == f"{SELECT_THEME_LABEL} (nord)"


def test_show_theme_screen_pushes_modal_with_available_themes_and_current() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_current_theme_name.return_value = "nord"
    mock_screen.app.available_theme_names.return_value = ["dracula", "nord"]
    provider = ThemeCommandProvider(mock_screen)

    provider._show_theme_screen()

    (pushed_screen,), _ = mock_screen.app.push_screen.call_args
    assert isinstance(pushed_screen, ThemeSelectionScreen)
    assert pushed_screen._theme_names == ["dracula", "nord"]
    assert pushed_screen._current_theme_name == "nord"


async def test_discover_yields_a_hit_with_undecorated_canonical_text() -> None:
    """The command's `canonical_text` (used to recall it later, see
    `docs/specs/command-palette-from-prompt.md`) stays the undecorated `SELECT_THEME_LABEL`
    root, since the displayed label's parenthetical current-theme suffix changes with the
    active theme and wouldn't reliably match this same command again if recorded verbatim.
    """
    mock_screen = MagicMock()
    mock_screen.app.get_current_theme_name.return_value = "nord"
    provider = ThemeCommandProvider(mock_screen)

    hits = [hit async for hit in provider.discover()]

    assert len(hits) == 1
    hit = hits[0]
    assert isinstance(hit, DiscoveryHit)
    assert str(hit.display) == f"{SELECT_THEME_LABEL} (nord)"
    assert hit.text == SELECT_THEME_LABEL


async def test_search_filters_by_query() -> None:
    mock_screen = MagicMock()
    mock_screen.app.get_current_theme_name.return_value = "nord"
    provider = ThemeCommandProvider(mock_screen)

    hits = [hit async for hit in provider.search("theme")]
    no_hits = [hit async for hit in provider.search("not-a-real-command-xyz")]

    assert any("theme" in str(hit.text).lower() for hit in hits)
    assert no_hits == []
