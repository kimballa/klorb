# © Copyright 2026 Aaron Kimball
"""Typeahead command palette rendered inline above the prompt input, driven by a leading
`>` in the prompt textbox rather than the `ctrl+p` modal. See docs/specs/command-palette-from-prompt.md.
"""

from inspect import isclass
from typing import Sequence

from textual.app import App
from textual.command import DiscoveryHit, Hit, Provider
from textual.widgets import OptionList
from textual.widgets.option_list import Option

PALETTE_PREFIX = ">"
PROMPT_PALETTE_ID = "prompt-palette"


def _provider_classes(app: App[object]) -> set[type[Provider]]:
    """Resolve `app.COMMANDS`/`app.screen.COMMANDS` into a set of `Provider` subclasses,
    accepting either a `Provider` subclass or a zero-arg callable returning one.
    """
    classes: set[type[Provider]] = set()
    for entry in set(app.COMMANDS) | set(app.screen.COMMANDS):
        if isclass(entry) and issubclass(entry, Provider):
            classes.add(entry)
        else:
            # `entry` is a zero-arg factory returning a `Provider` subclass here (Textual's
            # lazy-provider convention, e.g. `get_system_commands_provider`); mypy can't
            # narrow `type[Provider] | Callable[[], type[Provider]]` through `isclass()`
            # the way `isinstance()` narrows a plain `Union`, so it still considers `entry`
            # possibly a bare `type[Provider]` (whose constructor needs a `screen` argument)
            # in this branch.
            classes.add(entry())  # type: ignore[call-arg,arg-type]
    return classes


def _sort_key(hit: Hit | DiscoveryHit) -> tuple[float, str]:
    """Descending match score, then ascending (case-insensitive) alphabetical `text` as a
    tiebreak.
    """
    return (-hit.score, str(hit.text).casefold())


async def gather_palette_hits(app: App[object], query: str) -> list[Hit | DiscoveryHit]:
    """Collect every `Hit`/`DiscoveryHit` that `app`'s registered command providers yield for
    `query`, sorted by descending match score, breaking ties alphabetically (case-insensitive,
    A-Z) by each hit's plain `text` (`_sort_key`). A `DiscoveryHit` (yielded for an empty
    `query`, i.e. the bare `>` full listing) always scores `0.0`, so with no query to rank by,
    every hit ties and the tiebreak alone determines the order.

    Each provider is freshly constructed and torn down per call rather than cached across
    keystrokes.
    """
    hits: list[Hit | DiscoveryHit] = []
    for provider_cls in _provider_classes(app):
        provider = provider_cls(app.screen)
        provider._post_init()
        async for hit in provider._search(query):
            hits.append(hit)
        await provider._shutdown()
    hits.sort(key=_sort_key)
    return hits


class PaletteOption(Option):
    """An `OptionList` row that carries the `Hit`/`DiscoveryHit` it renders, so selecting it
    can recover the hit's `command` callback and canonical `text`.
    """

    def __init__(self, hit: Hit | DiscoveryHit) -> None:
        super().__init__(hit.prompt)
        self.hit = hit


class PromptPalette(OptionList, can_focus=False):
    """Inline list of matching palette commands, shown directly above the prompt input.

    Never focused (`can_focus=False`): the prompt input keeps focus the whole time, and
    `PromptInput` drives this widget's highlight programmatically (`move_highlight`) in
    response to up/down arrow keys rather than `OptionList`'s own key bindings.
    """

    DEFAULT_CSS = """
    PromptPalette {
        display: none;
        width: auto;
        max-width: 60;
        height: auto;
        max-height: 10;
        border: round $accent;
        background: $panel;
    }
    """

    def show_hits(self, hits: Sequence[Hit | DiscoveryHit]) -> None:
        """Replace the displayed rows with `hits` and highlight the first one."""
        self.clear_options()
        self.add_options([PaletteOption(hit) for hit in hits])
        if hits:
            self.highlighted = 0
        self.display = True

    def hide(self) -> None:
        """Hide the popup and drop its current rows."""
        self.display = False
        self.clear_options()

    @property
    def current_hit(self) -> Hit | DiscoveryHit | None:
        """The `Hit`/`DiscoveryHit` for the currently-highlighted row, or `None` if the popup
        has no rows.
        """
        if self.highlighted is None:
            return None
        option = self.get_option_at_index(self.highlighted)
        assert isinstance(option, PaletteOption)
        return option.hit

    def move_highlight(self, direction: int) -> None:
        """Move the highlighted row by one, up (`direction < 0`) or down (`direction > 0`),
        wrapping via `OptionList`'s own cursor actions.
        """
        if direction < 0:
            self.action_cursor_up()
        else:
            self.action_cursor_down()
