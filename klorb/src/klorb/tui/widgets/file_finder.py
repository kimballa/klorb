# © Copyright 2026 Aaron Kimball
"""Inline `@`-mention fuzzy file finder rendered above the prompt input, driven by an active
`@mention` at the cursor rather than a leading prefix like `klorb.tui.widgets.palette`'s `>`
command palette. See docs/specs/at-mention-file-inlining.md's "Interactive fuzzy finder"
section.
"""

from dataclasses import dataclass
from typing import Sequence

from textual.content import Content
from textual.fuzzy import Matcher
from textual.widgets import OptionList
from textual.widgets.option_list import Option

FILE_FINDER_ID = "file-finder"
MAX_FILE_FINDER_MATCHES = 6
"""How many matches the popup shows at once -- also its maximum visible row count."""

_ROW_WIDTH_PADDING = 4
"""Estimated horizontal space `FileFinderPanel`'s `OptionList` chrome (a scrollbar, plus each
`Option`'s own internal padding) consumes around a row's rendered text -- subtracted from the
panel's own width before splitting a row's path into a truncatable dir part and fixed file
part (see `split_finder_row`), mirroring `InteractionsMixin`'s
`_COMMAND_PREVIEW_WIDTH_PADDING`."""
_MIN_ROW_WIDTH = 20
"""Floor for the width estimate above, so a very narrow terminal still gets a usable (if
aggressively truncated) row instead of a degenerate near-zero width."""


@dataclass(frozen=True)
class MentionQuery:
    """Where the currently active `@mention` starts on the prompt's current line (the column
    of `@` itself) and what's been typed after it up to the cursor -- the finder's search
    query."""

    start_column: int
    query: str


def detect_mention_query(line: str, cursor_column: int) -> MentionQuery | None:
    """Find the `@mention` (if any) `cursor_column` sits inside of on `line`: scans backward
    for the nearest `@` not separated from it by whitespace, itself preceded by the start of
    the line or whitespace (so `user@example.com` mid-word doesn't trigger). Mirrors
    `klorb.session.mixins.mentions._AT_MENTION_RE`'s unquoted-mention boundary rule --
    restricting the scan to one line is equivalent, since a newline satisfies that regex's own
    `\\s` boundary check the same way a line start does here, and neither branch of the regex
    can match text containing a newline.
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


def filter_workspace_files(
    paths: Sequence[str], query: str, *, limit: int = MAX_FILE_FINDER_MATCHES,
) -> list[str]:
    """Return up to `limit` of `paths` that fuzzy-match `query`, in display order: alphabetical
    for an empty query (nothing to rank), otherwise by descending `textual.fuzzy.Matcher`
    score. Mirrors `klorb.tui.commands.model_commands.filter_model_names`.
    """
    if not query:
        return sorted(paths)[:limit]
    matcher = Matcher(query)
    scored = sorted(((matcher.match(path), path) for path in paths), key=lambda pair: -pair[0])
    return [path for score, path in scored if score > 0][:limit]


def escape_mention_path(rel_path: str) -> str:
    r"""Escape `rel_path` for insertion right after an `@` in the prompt: backslash first (so
    the escapes this introduces aren't re-escaped by the later replacements), then double
    quotes, then spaces -- e.g. `foo bar.txt` -> `foo\ bar.txt`. The exact inverse of
    `klorb.session.mixins.mentions.unescape_mention_filename`.
    """
    return rel_path.replace("\\", "\\\\").replace('"', '\\"').replace(" ", "\\ ")


def build_mention_insertion(query_start_column: int, cursor_column: int, rel_path: str) -> tuple[str, int]:
    """Return `(insertion_text, new_cursor_column)` for replacing the current line's
    `[query_start_column, cursor_column)` span -- the active `@query` -- with an escaped
    `@mention` of `rel_path` plus a trailing space, so the user can keep typing immediately.
    The caller applies `insertion_text` via `TextArea.replace()` at those two column positions
    on the cursor's row and moves the cursor to `new_cursor_column`.
    """
    insertion = f"@{escape_mention_path(rel_path)} "
    return insertion, query_start_column + len(insertion)


def split_finder_row(rel_path: str, available_width: int) -> tuple[str, str]:
    """Split `rel_path` into a (possibly truncated) directory part and a fixed, always-fully-
    visible file part (with its own leading `/` whenever a directory is present), so a deeply
    nested path reads as `"some/path/to.../file.txt"` instead of overflowing `available_width`.
    Truncation (when the full path doesn't fit) drops characters off the *end* of the directory
    part and appends `"..."`, keeping the path's leading context; `available_width <= 0` means
    the width is unknown and no truncation is applied.
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
    return dir_part[:budget] + ellipsis, file_part


def _row_content(rel_path: str, available_width: int) -> Content:
    """Build the styled row label for `rel_path`: the directory part (if any) in a muted
    color, the file part (including its leading `/`) in the normal foreground."""
    dir_display, file_display = split_finder_row(rel_path, available_width)
    if not dir_display:
        return Content(file_display)
    return Content.assemble((dir_display, "$text-muted"), file_display)


class FileFinderOption(Option):
    """An `OptionList` row that carries the workspace-relative path it renders, so selecting it
    can recover the plain path to insert."""

    def __init__(self, rel_path: str, available_width: int) -> None:
        super().__init__(_row_content(rel_path, available_width))
        self.rel_path = rel_path


class FileFinderPanel(OptionList, can_focus=False):
    """List of matching workspace files, shown directly above the prompt input while the
    cursor sits inside an `@mention`.

    Never focused (`can_focus=False`): the prompt input keeps focus the whole time, and
    `PromptInput` drives this widget's highlight programmatically (`move_highlight`) in
    response to up/down arrow keys, mirroring `klorb.tui.widgets.palette.PromptPalette`.
    """

    DEFAULT_CSS = """
    FileFinderPanel {
        display: none;
        width: 1fr;
        height: auto;
        max-height: 7;
        border-top: solid $accent;
        background: $panel;
    }
    """

    def show_matches(self, paths: Sequence[str]) -> None:
        """Replace the displayed rows with `paths` and highlight the first one."""
        self.clear_options()
        available_width = max(self.size.width - _ROW_WIDTH_PADDING, _MIN_ROW_WIDTH)
        self.add_options([FileFinderOption(path, available_width) for path in paths])
        if paths:
            self.highlighted = 0
        self.display = True

    def hide(self) -> None:
        """Hide the popup and drop its current rows."""
        self.display = False
        self.clear_options()

    @property
    def current_path(self) -> str | None:
        """The workspace-relative path of the currently-highlighted row, or `None` if the
        popup has no rows."""
        if self.highlighted is None:
            return None
        option = self.get_option_at_index(self.highlighted)
        assert isinstance(option, FileFinderOption)
        return option.rel_path

    def move_highlight(self, direction: int) -> None:
        """Move the highlighted row by one, up (`direction < 0`) or down (`direction > 0`),
        wrapping via `OptionList`'s own cursor actions."""
        if direction < 0:
            self.action_cursor_up()
        else:
            self.action_cursor_down()
