# © Copyright 2026 Aaron Kimball
"""Command palette provider and modal for switching the active model, mirroring
`klorb.tui.theme_commands`'s "one command that names the current value, opens a modal" shape
— see docs/adrs/single-change-model-command-with-typeahead-modal.md for why models get a
dedicated typeahead-filterable modal instead of one palette entry per model."""

from typing import Protocol, cast

from textual.app import ComposeResult
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Vertical
from textual.content import Content
from textual.events import Key
from textual.fuzzy import Matcher
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

CHANGE_MODEL_LABEL = "Change model"
MODEL_HEADER_TEXT = "Model:"
MODEL_FILTER_INPUT_ID = "model-filter"
MODEL_OPTION_LIST_ID = "model-options"
MODEL_FILTER_PLACEHOLDER = "Type to filter..."
CURRENT_MODEL_MARKER = " (*)"


class SupportsModelSelection(Protocol):
    """Structural interface for an App that can have its active model changed."""

    def get_current_model_name(self) -> str:
        """Return the identifier of the currently active model."""

    def available_model_names(self) -> list[str]:
        """Return every model name available for selection, sorted for a stable display order."""

    def select_model(self, name: str) -> None:
        """Make `name` the active model for subsequent prompts."""


def filter_model_names(model_names: list[str], query: str) -> list[str]:
    """Return the subset of `model_names` that fuzzy-match `query`, in display order:
    alphabetical for an empty query (nothing to rank), otherwise by descending
    `textual.fuzzy.Matcher` score. Pulled out of `ModelSelectionScreen` so it's testable
    without mounting a Textual widget tree.
    """
    if not query:
        return sorted(model_names)
    matcher = Matcher(query)
    scored = sorted(((matcher.match(name), name) for name in model_names), key=lambda pair: -pair[0])
    return [name for score, name in scored if score > 0]


class _ModelOptionList(OptionList, can_focus=False):
    """The filtered model list. Never focused: `ModelSelectionScreen`'s filter `Input` keeps
    focus the whole time, and up/down/enter are forwarded here programmatically — the same
    split-focus shape `klorb.tui.palette.PromptPalette` uses for the inline `>` palette."""


class ModelSelectionScreen(ModalScreen[None]):
    """Modal listing every available model in a filterable `OptionList`, with a text `Input`
    above it that narrows the list by fuzzy match as the user types (`textual.fuzzy.Matcher`,
    the same matcher `Provider.matcher()` wraps for the rest of the command palette). Up/down
    arrow keys move the highlight and Enter confirms — both work while the filter box keeps
    keyboard focus. The currently-active model is marked with a trailing `(*)`. Escape
    dismisses without making a selection.
    """

    CSS = """
    ModelSelectionScreen {
        align: center middle;
    }

    ModelSelectionScreen Vertical {
        width: auto;
        height: auto;
        max-height: 80%;
        border: round $accent;
    }

    #model-header {
        padding: 0 1;
        text-style: bold;
    }

    ModelSelectionScreen Input {
        border: none;
        margin: 0 1;
    }

    ModelSelectionScreen OptionList {
        border: none;
        max-height: 20;
    }
    """

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(self, model_names: list[str], current_model_name: str) -> None:
        super().__init__()
        self._model_names = model_names
        self._current_model_name = current_model_name
        self._filtered_names: list[str] = list(model_names)

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(MODEL_HEADER_TEXT, id="model-header"),
            Input(placeholder=MODEL_FILTER_PLACEHOLDER, id=MODEL_FILTER_INPUT_ID),
            _ModelOptionList(id=MODEL_OPTION_LIST_ID),
        )

    def on_mount(self) -> None:
        self._refresh_options("")
        self.query_one(f"#{MODEL_FILTER_INPUT_ID}", Input).focus()

    def _refresh_options(self, query: str) -> None:
        """Recompute `self._filtered_names` (via `filter_model_names`) for `query` and
        repopulate the option list, with the matched substrings highlighted for a non-empty
        query — the same score-then-highlight treatment `gather_palette_hits` gives every
        other palette command.
        """
        option_list = self.query_one(f"#{MODEL_OPTION_LIST_ID}", _ModelOptionList)
        self._filtered_names = filter_model_names(self._model_names, query)
        matcher = Matcher(query) if query else None
        options = [
            self._render_option(name, matcher.highlight(name) if matcher else Content(name))
            for name in self._filtered_names
        ]
        option_list.clear_options()
        option_list.add_options(options)
        if self._filtered_names:
            option_list.highlighted = 0

    def _render_option(self, name: str, content: Content) -> Option:
        if name == self._current_model_name:
            content = Content.assemble(content, CURRENT_MODEL_MARKER)
        return Option(content)

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh_options(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._confirm_highlighted()

    def on_key(self, event: Key) -> None:
        """Forward up/down to the option list's highlight while the filter `Input` keeps
        focus — `Input` doesn't bind either key itself, so they'd otherwise do nothing."""
        option_list = self.query_one(f"#{MODEL_OPTION_LIST_ID}", _ModelOptionList)
        if event.key == "up":
            event.stop()
            option_list.action_cursor_up()
        elif event.key == "down":
            event.stop()
            option_list.action_cursor_down()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._select(self._filtered_names[event.option_index])

    def _confirm_highlighted(self) -> None:
        option_list = self.query_one(f"#{MODEL_OPTION_LIST_ID}", _ModelOptionList)
        if option_list.highlighted is None or not self._filtered_names:
            return
        self._select(self._filtered_names[option_list.highlighted])

    def _select(self, name: str) -> None:
        cast(SupportsModelSelection, self.app).select_model(name)
        self.dismiss()


class ModelCommandProvider(Provider):
    """Offers a single `"Change model (<current>)"` command via the command palette that opens
    `ModelSelectionScreen`, replacing the previous one-entry-per-model listing — see
    docs/adrs/single-change-model-command-with-typeahead-modal.md.
    """

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        label = self._label()
        score = matcher.match(label)
        if score > 0:
            yield Hit(score, matcher.highlight(label), self._show_model_screen, text=CHANGE_MODEL_LABEL)

    async def discover(self) -> Hits:
        yield DiscoveryHit(self._label(), self._show_model_screen, text=CHANGE_MODEL_LABEL)

    def _label(self) -> str:
        """`CHANGE_MODEL_LABEL` plus the currently-active model's name in parentheses.

        `canonical_text` (see `search`/`discover`'s `text=` argument) deliberately stays the
        undecorated `CHANGE_MODEL_LABEL` root rather than this decorated label — a
        palette-from-prompt recall (see `docs/specs/command-palette-from-prompt.md`) needs to
        match this same command again later, and the parenthetical current-model suffix
        changes with `get_current_model_name()`, so recording it verbatim wouldn't reliably do
        that.
        """
        current = cast(SupportsModelSelection, self.app).get_current_model_name()
        return f"{CHANGE_MODEL_LABEL} ({current})"

    def _show_model_screen(self) -> None:
        app = cast(SupportsModelSelection, self.app)
        self.app.push_screen(ModelSelectionScreen(app.available_model_names(), app.get_current_model_name()))
