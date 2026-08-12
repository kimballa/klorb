# © Copyright 2026 Aaron Kimball
"""Command palette provider for setting thinking state (Off/Low/Medium/High)."""

from typing import Callable, Protocol, cast

from textual.app import ComposeResult
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static

from klorb.session import ThinkingEffort

THINKING_STATE_LABEL = "Set thinking"
THINKING_STATE_OPTIONS: tuple[str, ...] = ("off", "low", "medium", "high")
THINKING_STATE_HEADER_TEXT = "Thinking state:"
THINKING_STATE_OPTION_LIST_ID = "thinking-state-options"
CURRENT_THINKING_STATE_MARKER = " *"


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


class ThinkingStateScreen(ModalScreen[None]):
    """Modal listing the four thinking states (Off/Low/Medium/High), stacked vertically
    in an `OptionList` under a header, so the user can move between them with the up/down
    arrow keys and confirm with Enter. The currently-active state is marked with a
    trailing `*`. Escape dismisses without making a selection.
    """

    CSS = """
    ThinkingStateScreen {
        align: center middle;
    }

    ThinkingStateScreen Vertical {
        width: auto;
        height: auto;
        border: round $accent;
    }

    #thinking-state-header {
        padding: 0 1;
        text-style: bold;
    }

    ThinkingStateScreen OptionList {
        border: none;
    }
    """

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(self, thinking_enabled: bool, current_effort: ThinkingEffort) -> None:
        super().__init__()
        self._thinking_enabled = thinking_enabled
        self._current_effort = current_effort

    def compose(self) -> ComposeResult:
        current_state = "off" if not self._thinking_enabled else self._current_effort
        options = (
            f"{state}{CURRENT_THINKING_STATE_MARKER}" if state == current_state else state
            for state in THINKING_STATE_OPTIONS
        )
        yield Vertical(
            Static(THINKING_STATE_HEADER_TEXT, id="thinking-state-header"),
            OptionList(*options, id=THINKING_STATE_OPTION_LIST_ID),
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        app = cast(SupportsThinkingConfig, self.app)
        if event.option_index == 0:
            app.set_thinking_enabled(False)
        else:
            app.set_thinking_enabled(True)
            app.set_thinking_effort(THINKING_STATE_OPTIONS[event.option_index])  # type: ignore[arg-type]
        self.dismiss()


class ThinkingCommandProvider(Provider):
    """Offers a single merged thinking-state command (Off/Low/Medium/High) via the command palette."""

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        label, (callback, canonical_text) = self._command()
        score = matcher.match(label)
        if score > 0:
            yield Hit(score, matcher.highlight(label), callback, text=canonical_text)

    async def discover(self) -> Hits:
        label, (callback, canonical_text) = self._command()
        yield DiscoveryHit(label, callback, text=canonical_text)

    def _command(self) -> tuple[str, tuple[Callable[[], None], str]]:
        """Return the display label and `(callback, canonical_text)` pair for the
        merged thinking-state command. The label shows the current state in
        parentheses (e.g. ``Set thinking (Medium)``).

        ``canonical_text`` is always the undecorated ``THINKING_STATE_LABEL`` so that
        recall from the command-palette-from-prompt feature matches regardless of
        which state was current when the command was recorded.
        """
        thinking_enabled = cast(SupportsThinkingConfig, self.app).get_thinking_enabled()
        current_effort = cast(SupportsThinkingConfig, self.app).get_thinking_effort()
        current_display = "Off" if not thinking_enabled else current_effort.title()
        label = f"{THINKING_STATE_LABEL} ({current_display})"
        return label, (self._show_thinking_state_screen, THINKING_STATE_LABEL)

    def _show_thinking_state_screen(self) -> None:
        app = cast(SupportsThinkingConfig, self.app)
        self.app.push_screen(ThinkingStateScreen(app.get_thinking_enabled(), app.get_thinking_effort()))
