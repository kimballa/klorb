# © Copyright 2026 Aaron Kimball
"""Command palette provider for toggling thinking on/off and choosing its effort level."""

from functools import partial
from typing import Callable, Protocol, cast

from textual.app import ComposeResult
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static

from klorb.session import ThinkingEffort

ENABLE_THINKING_LABEL = "Enable thinking"
DISABLE_THINKING_LABEL = "Disable thinking"
SET_THINKING_EFFORT_LABEL = "Set thinking effort"
THINKING_EFFORT_LEVELS: tuple[ThinkingEffort, ...] = ("low", "medium", "high")
THINKING_EFFORT_HEADER_TEXT = "Thinking effort level:"
THINKING_EFFORT_OPTION_LIST_ID = "thinking-effort-options"
CURRENT_THINKING_EFFORT_MARKER = " *"


class SupportsThinkingConfig(Protocol):
    """Structural interface for an App that can have its thinking config changed."""

    def get_thinking_effort(self) -> ThinkingEffort:
        """Return the reasoning effort level currently configured for subsequent prompts."""

    def get_thinking_enabled(self) -> bool:
        """Return whether extended-thinking requests are currently enabled."""

    def set_thinking_enabled(self, enabled: bool) -> None:
        """Enable or disable extended-thinking requests for subsequent prompts."""

    def set_thinking_effort(self, effort: ThinkingEffort) -> None:
        """Set the reasoning effort level used for subsequent prompts."""


class ThinkingEffortScreen(ModalScreen[None]):
    """Modal listing the three `ThinkingEffort` levels, stacked vertically in an
    `OptionList` under a header, so the user can move between them with the up/down
    arrow keys and confirm with Enter. The currently-active level is marked with a
    trailing `*`. Escape dismisses without making a selection.
    """

    CSS = """
    ThinkingEffortScreen {
        align: center middle;
    }

    ThinkingEffortScreen Vertical {
        width: auto;
        height: auto;
        border: round $accent;
    }

    #thinking-effort-header {
        padding: 0 1;
        text-style: bold;
    }

    ThinkingEffortScreen OptionList {
        border: none;
    }
    """

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(self, current_effort: ThinkingEffort) -> None:
        super().__init__()
        self._current_effort = current_effort

    def compose(self) -> ComposeResult:
        options = (
            f"{level}{CURRENT_THINKING_EFFORT_MARKER}" if level == self._current_effort else level
            for level in THINKING_EFFORT_LEVELS
        )
        yield Vertical(
            Static(THINKING_EFFORT_HEADER_TEXT, id="thinking-effort-header"),
            OptionList(*options, id=THINKING_EFFORT_OPTION_LIST_ID),
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        cast(SupportsThinkingConfig, self.app).set_thinking_effort(
            THINKING_EFFORT_LEVELS[event.option_index])
        self.dismiss()


class ThinkingCommandProvider(Provider):
    """Offers thinking on/off and effort-level commands via the command palette."""

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for label, (callback, canonical_text) in self._commands().items():
            score = matcher.match(label)
            if score > 0:
                yield Hit(score, matcher.highlight(label), callback, text=canonical_text)

    async def discover(self) -> Hits:
        for label, (callback, canonical_text) in self._commands().items():
            yield DiscoveryHit(label, callback, text=canonical_text)

    def _commands(self) -> dict[str, tuple[Callable[[], None], str]]:
        """Return a mapping of command palette display text to its `(callback,
        canonical_text)` pair. Only the toggle command that reflects a valid state
        transition is offered: `Enable thinking` when thinking is currently off, `Disable
        thinking` when it's on.

        `canonical_text` is what a caller (e.g. palette-from-prompt, see
        `docs/specs/command-palette-from-prompt.md`) should record as "what the user typed"
        for recall purposes, which for the toggle commands is just their own display text
        (selecting `Disable thinking` should recall `Disable thinking`) but for `set_effort_label`
        is always the undecorated `SET_THINKING_EFFORT_LABEL` root — the displayed text's
        parenthetical current-value suffix changes with `current_effort`, so recording it
        verbatim wouldn't reliably match this same command again later.
        """
        thinking_enabled = cast(SupportsThinkingConfig, self.app).get_thinking_enabled()
        toggle_label = DISABLE_THINKING_LABEL if thinking_enabled else ENABLE_THINKING_LABEL
        current_effort = cast(SupportsThinkingConfig, self.app).get_thinking_effort()
        set_effort_label = f"{SET_THINKING_EFFORT_LABEL} ({current_effort.title()})"
        return {
            toggle_label: (partial(self._set_thinking_enabled, not thinking_enabled), toggle_label),
            set_effort_label: (self._show_thinking_effort_screen, SET_THINKING_EFFORT_LABEL),
        }

    def _set_thinking_enabled(self, enabled: bool) -> None:
        cast(SupportsThinkingConfig, self.app).set_thinking_enabled(enabled)

    def _show_thinking_effort_screen(self) -> None:
        app = cast(SupportsThinkingConfig, self.app)
        self.app.push_screen(ThinkingEffortScreen(app.get_thinking_effort()))
