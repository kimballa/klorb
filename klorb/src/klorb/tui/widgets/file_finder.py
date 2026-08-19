# © Copyright 2026 Aaron Kimball
"""Inline `@`-mention fuzzy file finder rendered above the prompt input, driven by an active
`@mention` at the cursor rather than a leading prefix like `klorb.tui.widgets.palette`'s `>`
command palette. See docs/specs/at-mention-file-inlining.md.
"""

from dataclasses import dataclass
from typing import Sequence

from textual.content import Content
from textual.events import Resize
from textual.fuzzy import Matcher
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from klorb.session.mixins.mentions import TRAILING_MENTION_PUNCTUATION
from klorb.tui.constants import PROMPT_INPUT_ID

FILE_FINDER_ID = "file-finder"
MAX_FILE_FINDER_MATCHES = 25
"""How many ranked matches the popup keeps, scrollable within its fixed on-screen height."""

_DIRECTORY_SCORE_BUMP = 0.1
"""Added to a directory candidate's fuzzy-match score before ranking, so a directory whose
name matches about as well as a file surfaces above it."""

_DIRECTORY_DEPTH_DECAY = 0.85
"""Multiplies a directory candidate's score once per `/` in its path, so among
similarly-scored directories the one nearest the workspace root outranks one nested many
levels deep. Multiplicative rather than a flat subtraction because `Matcher.match`'s real
output isn't normalized to a fixed range."""

_ROW_WIDTH_PADDING = 4
"""Estimated horizontal space `FileFinderPanel`'s `OptionList` chrome (a scrollbar, plus each
`Option`'s own internal padding) consumes around a row's rendered text."""
_MIN_ROW_WIDTH = 20
"""Floor for the width estimate above, so a very narrow terminal still gets a usable row
instead of a degenerate near-zero width."""


@dataclass(frozen=True)
class MentionQuery:
    """Where the currently active `@mention` starts on the prompt's current line (the column
    of `@` itself) and what's been typed after it up to the cursor."""

    start_column: int
    query: str


def detect_mention_query(line: str, cursor_column: int) -> MentionQuery | None:
    """Find the `@mention` (if any) `cursor_column` sits inside of on `line`: scans backward
    for the nearest `@` not separated from it by whitespace, itself preceded by the start of
    the line or whitespace.
    """
    for i in range(cursor_column - 1, -1, -1):
        ch = line[i]
        if ch.isspace():
            return None
        if ch == "@":
            if i > 0 and not line[i - 1].isspace():
                return None
            return MentionQuery(start_column=i, query=line[i + 1:cursor_column])
    return None


@dataclass(frozen=True)
class FinderMatch:
    """One row the fuzzy finder can show: a workspace-relative path plus whether it names a
    directory rather than a file. A file row is a leaf mention target; a directory row instead
    narrows the active query into that subtree when selected, since a bare directory isn't a
    valid `@mention` target."""

    path: str
    is_dir: bool


def _ancestor_directories(paths: Sequence[str]) -> set[str]:
    """Every directory that contains, directly or transitively, one of `paths`."""
    directories: set[str] = set()
    for path in paths:
        parts = path.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            directories.add("/".join(parts[:depth]))
    return directories


def _split_query_directory(query: str) -> tuple[str, str]:
    """Split `query` at its last `/` into a literal directory prefix (including the trailing
    `/`, or `""` if `query` has no `/`) and the remaining fuzzy-match text. A query fresh off
    selecting a directory match splits into a prefix and an empty remainder."""
    idx = query.rfind("/")
    if idx == -1:
        return "", query
    return query[:idx + 1], query[idx + 1:]


def _breadth_first_matches(
    candidates: set[str], directories: set[str], dir_prefix: str, limit: int,
) -> list[FinderMatch]:
    """Order `candidates` with the immediate contents of `dir_prefix` first, followed by
    everything nested deeper, which fills in only up to `limit` total. Used when there's no
    fuzzy-match text to rank by, so browsing a directory always shows everything sitting
    directly in it instead of letting a large subtree's grandchildren crowd it out of the
    truncated list.
    """
    def relative_depth(path: str) -> int:
        return path[len(dir_prefix):].count("/")

    def sort_key(path: str) -> tuple[bool, bool, int, str]:
        depth = relative_depth(path)
        return (depth != 0, path not in directories, depth, path)

    ordered = sorted(candidates, key=sort_key)
    immediate_count = sum(1 for path in candidates if relative_depth(path) == 0)
    cutoff = max(limit, immediate_count)
    return [FinderMatch(path, path in directories) for path in ordered[:cutoff]]


def filter_workspace_files(
    paths: Sequence[str], query: str, *, limit: int = MAX_FILE_FINDER_MATCHES,
) -> list[FinderMatch]:
    """Return up to `limit` files and ancestor directories from `paths` that match `query`.
    `query` splits at its last `/` into a literal directory prefix and a remaining fuzzy-match
    fragment: candidates are first narrowed to real descendants of that prefix, so a query built
    by selecting a directory match actually scopes to that subtree instead of fuzzy-matching the
    directory's name against every workspace path. Within that scope, an empty fragment falls back
    to `_breadth_first_matches`; otherwise candidates rank by descending `textual.fuzzy.Matcher`
    score of the fragment against each candidate's path *relative to the prefix*.
    """
    directories = _ancestor_directories(paths)
    universe = set(paths) | directories
    dir_prefix, fragment = _split_query_directory(query)
    scoped = {path for path in universe if path.startswith(dir_prefix)} if dir_prefix else universe

    if not fragment:
        return _breadth_first_matches(scoped, directories, dir_prefix, limit)

    matcher = Matcher(fragment)

    def score_of(path: str, is_dir: bool) -> float:
        base = matcher.match(path[len(dir_prefix):])
        if base <= 0 or not is_dir:
            return base
        return (base + _DIRECTORY_SCORE_BUMP) * (_DIRECTORY_DEPTH_DECAY ** path.count("/"))

    scored = ((score_of(path, path in directories), path, path in directories) for path in scoped)
    ranked = sorted(scored, key=lambda triple: -triple[0])
    return [FinderMatch(path, is_dir) for score, path, is_dir in ranked if score > 0][:limit]


def escape_mention_path(rel_path: str) -> str:
    r"""Escape `rel_path` for insertion right after an `@` in the prompt: backslash first (so
    the escapes this introduces aren't re-escaped by the later replacements), then double
    quotes, then spaces. The exact inverse of
    `klorb.session.mixins.mentions.unescape_mention_filename`.
    """
    return rel_path.replace("\\", "\\\\").replace('"', '\\"').replace(" ", "\\ ")


def _escape_quoted_mention_path(rel_path: str) -> str:
    r"""Escape `rel_path` for insertion inside an `@"..."` quoted mention: backslash first, then
    double quotes."""
    return rel_path.replace("\\", "\\\\").replace('"', '\\"')


def build_mention_insertion(query_start_column: int, cursor_column: int, rel_path: str) -> tuple[str, int]:
    """Return `(insertion_text, new_cursor_column)` for replacing the current line's
    `[query_start_column, cursor_column)` span -- the active `@query` -- with an `@mention` of
    `rel_path` plus a trailing space, so the user can keep typing immediately. Uses the quoted
    form (`@"rel_path" `) when `rel_path` itself ends in a `TRAILING_MENTION_PUNCTUATION`
    character, since `strip_trailing_mention_punctuation` would otherwise trim that character
    back off an unquoted insertion when the prompt is later resolved; every other path uses the
    ordinary escaped unquoted form. The caller applies `insertion_text` via `TextArea.replace()`
    at those two column positions on the cursor's row and moves the cursor to
    `new_cursor_column`.
    """
    if rel_path and rel_path[-1] in TRAILING_MENTION_PUNCTUATION:
        insertion = f'@"{_escape_quoted_mention_path(rel_path)}" '
    else:
        insertion = f"@{escape_mention_path(rel_path)} "
    return insertion, query_start_column + len(insertion)


def split_finder_row(rel_path: str, available_width: int) -> tuple[str, str]:
    """Split `rel_path` into a (possibly truncated) directory part and a fixed, always-fully-
    visible file part (with its own leading `/` whenever a directory is present), so a deeply
    nested path reads as `".../path/to/file.txt"` instead of overflowing `available_width`.
    Truncation (when the full path doesn't fit) drops characters off the *front* of the
    directory part and prepends `"..."`, keeping the segment immediately before the file part;
    `available_width <= 0` means the
    width is unknown and no truncation is applied.
    """
    idx = rel_path.rfind("/")
    if idx == -1:
        return "", rel_path
    dir_part = rel_path[:idx]
    file_part = rel_path[idx:]
    if available_width <= 0 or len(dir_part) + len(file_part) <= available_width:
        return dir_part, file_part
    ellipsis = "..."
    budget = available_width - len(file_part) - len(ellipsis)
    if budget <= 0:
        return ellipsis, file_part
    return ellipsis + dir_part[-budget:], file_part


def _row_content(match: FinderMatch, available_width: int) -> Content:
    """Build the styled row label for `match`: the directory part (if any) in a muted color,
    the file part (including its leading `/`) in the normal foreground, with a trailing `/`
    appended when `match` is itself a directory."""
    reserved = 1 if match.is_dir else 0
    dir_display, file_display = split_finder_row(match.path, max(available_width - reserved, 0))
    if match.is_dir:
        file_display += "/"
    if not dir_display:
        return Content(file_display)
    # `$foreground-muted`, not `$text-muted`: Textual drops `$text-*`'s alpha in an inline style.
    return Content.assemble((dir_display, "$foreground-muted"), file_display)


class FinderOption(Option):
    """Base class for `OptionList` rows. Carries the match
    datum (a `FinderMatch`, `SkillMatch`, or any other typed object) so the panel can recover
    it from a highlighted or clicked row."""

    def __init__(self, content: Content, match: object) -> None:
        super().__init__(content)
        self.match = match


class FileFinderOption(FinderOption):
    """An `OptionList` row that carries the `FinderMatch` it renders, so selecting it can
    recover the match to insert or drill into."""

    def __init__(self, match: FinderMatch, available_width: int) -> None:
        super().__init__(_row_content(match, available_width), match)


class FinderPanel(OptionList, can_focus=False):
    """Base class for inline fuzzy-finder panels rendered above the prompt input.

    Subclasses own the option type and the selection action; this base owns the shared
    chrome: show/hide, highlight movement, and click dispatch (via `_select_match`).
    """

    DEFAULT_CSS = """
    FinderPanel {
        display: none;
        width: 1fr;
        height: auto;
        max-height: 7;
        border: none;
        border-top: solid $accent;
        background: $panel;
        & > .option-list--option {
            padding: 0 1 0 0;
        }
    }
    """

    def on_mount(self) -> None:
        super().on_mount()
        self._last_matches: list[object] = []

    def show_matches(self, matches: Sequence[object]) -> None:
        """Replace the displayed rows with `matches` and highlight the first one."""
        self._last_matches = list(matches)
        self._render_matches()
        self.display = True

    def hide(self) -> None:
        """Hide the popup and drop its current rows."""
        self.display = False
        self._last_matches = []
        self.clear_options()

    def on_resize(self, event: Resize) -> None:
        """Re-render visible rows once the panel's real width is known: the first
        `show_matches` runs while the panel is still `display: none` (width 0), which drops
        trailing fields like a skill's description."""
        if not self.display or not self._last_matches:
            return
        self._render_matches(preserve_highlight=True)

    def _render_matches(self, *, preserve_highlight: bool = False) -> None:
        highlighted = self.highlighted if preserve_highlight else 0
        self.clear_options()
        available_width = max(self.size.width - _ROW_WIDTH_PADDING, _MIN_ROW_WIDTH)
        self.add_options(self._build_options(self._last_matches, available_width))
        if self._last_matches:
            self.highlighted = highlighted if highlighted is not None else 0

    @property
    def current_match(self) -> object | None:
        """The currently-highlighted row's match datum, or `None` if the popup has no rows."""
        if self.highlighted is None:
            return None
        option = self.get_option_at_index(self.highlighted)
        assert isinstance(option, FinderOption)
        return option.match

    def move_highlight(self, direction: int) -> None:
        """Move the highlighted row by one, up (`direction < 0`) or down (`direction > 0`),
        wrapping via `OptionList`'s own cursor actions."""
        if direction < 0:
            self.action_cursor_up()
        else:
            self.action_cursor_down()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """A mouse click (or other `OptionList` selection) on a row activates it exactly as
        Enter/Tab would, via `_select_match`."""
        event.stop()
        self._select_match()

    def _build_options(self, matches: Sequence[object], available_width: int) -> list[Option]:
        """Build typed `FinderOption` objects from `matches`. Subclasses override to construct
        their specific option type."""
        raise NotImplementedError

    def _select_match(self) -> None:
        """Dispatch a click or Enter/Tab selection to the right `PromptInput` method."""
        raise NotImplementedError


class FileFinderPanel(FinderPanel):
    """List of matching workspace files, shown directly above the prompt input while the
    cursor sits inside an `@mention`."""

    def _build_options(self, matches: Sequence[object], available_width: int) -> list[Option]:
        return [FileFinderOption(m, available_width) for m in matches]  # type: ignore[arg-type]

    def _select_match(self) -> None:
        """Dispatch a click or Enter/Tab selection to `PromptInput.select_finder_match`.
        Imported inline to break the circular import PromptInput itself has on this module."""
        from klorb.tui.widgets.prompt_input import PromptInput

        self.screen.query_one(f"#{PROMPT_INPUT_ID}", PromptInput).select_finder_match()
