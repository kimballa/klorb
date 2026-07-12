# © Copyright 2026 Aaron Kimball
"""Command palette provider and modal for picking a Textual theme, mirroring
`klorb.tui.thinking_commands`'s command-plus-modal shape but sourcing its option list from
`App.available_themes` instead of a fixed tuple.
"""

from typing import Protocol, cast

from textual.app import ComposeResult
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static

SELECT_THEME_LABEL = "Change theme"
THEME_HEADER_TEXT = "Theme:"
THEME_OPTION_LIST_ID = "theme-options"
CURRENT_THEME_MARKER = " (*)"


class SupportsThemeSelection(Protocol):
    """Structural interface for an App that can have its active Textual theme changed."""

    def get_current_theme_name(self) -> str:
        """Return the name of the currently-active Textual theme."""

    def available_theme_names(self) -> list[str]:
        """Return every theme name available for selection, sorted for a stable display order."""

    def select_theme(self, name: str) -> None:
        """Make `name` the active Textual theme for the rest of this process."""


class ThemeSelectionScreen(ModalScreen[None]):
    """Modal listing every available Textual theme, stacked vertically in an `OptionList`
    under a header, so the user can move between them with the up/down arrow keys and confirm
    with Enter. The currently-active theme is marked with a trailing `(*)`. Escape dismisses
    without making a selection.
    """

    CSS = """
    ThemeSelectionScreen {
        align: center middle;
    }

    ThemeSelectionScreen Vertical {
        width: auto;
        height: auto;
        max-height: 80%;
        border: round $accent;
    }

    #theme-header {
        padding: 0 1;
        text-style: bold;
    }

    ThemeSelectionScreen OptionList {
        border: none;
        max-height: 20;
    }
    """

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(self, theme_names: list[str], current_theme_name: str) -> None:
        super().__init__()
        self._theme_names = theme_names
        self._current_theme_name = current_theme_name

    def compose(self) -> ComposeResult:
        options = (
            f"{name}{CURRENT_THEME_MARKER}" if name == self._current_theme_name else name
            for name in self._theme_names
        )
        yield Vertical(
            Static(THEME_HEADER_TEXT, id="theme-header"),
            OptionList(*options, id=THEME_OPTION_LIST_ID),
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        cast(SupportsThemeSelection, self.app).select_theme(self._theme_names[event.option_index])
        self.dismiss()


class ThemeCommandProvider(Provider):
    """Offers a single `"Change theme (<current>)"` command via the command palette that opens
    `ThemeSelectionScreen`. Supersedes Textual's own built-in "Theme" system command — see
    `ReplApp.get_system_commands`, which drops that entry so there's exactly one theme-changing
    command instead of two competing pickers.
    """

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        label = self._label()
        score = matcher.match(label)
        if score > 0:
            yield Hit(score, matcher.highlight(label), self._show_theme_screen, text=SELECT_THEME_LABEL)

    async def discover(self) -> Hits:
        yield DiscoveryHit(self._label(), self._show_theme_screen, text=SELECT_THEME_LABEL)

    def _label(self) -> str:
        """`SELECT_THEME_LABEL` plus the currently-active theme's name in parentheses.

        `canonical_text` (see `search`/`discover`'s `text=` argument) deliberately stays the
        undecorated `SELECT_THEME_LABEL` root rather than this decorated label — a
        palette-from-prompt recall (see `docs/specs/command-palette-from-prompt.md`) needs to
        match this same command again later, and the parenthetical current-theme suffix changes
        with `get_current_theme_name()`, so recording it verbatim wouldn't reliably do that.
        """
        current = cast(SupportsThemeSelection, self.app).get_current_theme_name()
        return f"{SELECT_THEME_LABEL} ({current})"

    def _show_theme_screen(self) -> None:
        app = cast(SupportsThemeSelection, self.app)
        self.app.push_screen(ThemeSelectionScreen(app.available_theme_names(), app.get_current_theme_name()))
