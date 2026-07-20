# © Copyright 2026 Aaron Kimball
"""Pure formatting/data-munging helpers shared across `klorb.tui` modules: no widget state,
no `self`, just a value in and a value out.
"""

import importlib.resources
import json
import random
from pathlib import Path
from typing import Any

from rich.text import Text
from textual.containers import VerticalScroll
from textual.content import Content

from klorb.permissions.directory_access import DirRules
from klorb.tools.util.diff_lines import DiffHunk, DiffLine


def concat_dir_rules(base: DirRules, addition: DirRules) -> DirRules:
    """Concatenate `addition`'s deny/ask/allow entries onto `base`'s own, per category —
    never replacing what's already there. Used by `ReplApp._apply_workspace_config` to fold a
    freshly-(re)loaded `Workspace`'s config-file grants into a live `SessionConfig` without
    discarding any in-session-only grant ("Allow (this session)") already present; duplicate
    entries across the two are harmless (see docs/specs/permissions.md — evaluation is by
    category membership, not list position, so redundancy costs nothing but a few extra
    entries in the list)."""
    return DirRules(
        deny=list(base.deny) + list(addition.deny),
        ask=list(base.ask) + list(addition.ask),
        allow=list(base.allow) + list(addition.allow),
    )


def random_greeting() -> str:
    """Pick a random greeting from the packaged greetings.json resource."""
    try:
        text = importlib.resources.files("klorb.resources").joinpath("greetings.json")\
            .read_text(encoding="utf-8")
        greetings: list[str] = json.loads(text)
        return random.choice(greetings)
    except Exception:
        return "Roar! Let's go code a Thing!"


_SI_SUFFIXES = ("", "k", "M", "B")


def _round_to_2_sig_figs(value: float) -> float:
    """Round `value` (>= 1) to 2 significant figures, e.g. `1.43` -> `1.4`, `423` -> `420`."""
    if value < 10:
        return round(value, 1)
    if value < 100:
        return float(round(value))
    return float(round(value / 10) * 10)


def format_token_count(count: int) -> str:
    """Render `count` using SI-style suffixes at 2 significant figures once >= 1000, e.g.
    `1400` -> `"1.4k"`, `23000` -> `"23k"`, `423000` -> `"420k"`. Counts under 1000 are
    shown as a literal integer.
    """
    if count < 1000:
        return str(count)

    magnitude = 0
    value = float(count)
    while value >= 1000 and magnitude < len(_SI_SUFFIXES) - 1:
        value /= 1000
        magnitude += 1

    value = _round_to_2_sig_figs(value)
    if value >= 1000 and magnitude < len(_SI_SUFFIXES) - 1:
        value /= 1000
        magnitude += 1

    suffix = _SI_SUFFIXES[magnitude]
    if value == int(value):
        return f"{int(value)}{suffix}"
    return f"{value}{suffix}"


def summarize_reasoning_details(entries: list[dict[str, Any]]) -> str | None:
    """Return a compact indicator string for a turn's `reasoning_details` payload (see
    `klorb.message.Message.reasoning_details`), or `None` if every entry carries its own
    human-readable `text`/`summary` string -- content the `<Thinking>` block already shows in
    full, since a provider's plain-text `reasoning` delta is itself composed from those same
    entries. An entry with neither field (e.g. OpenRouter's opaque `reasoning.encrypted`
    type) carries information the `<Thinking>` block can't display, so any such entry makes
    the whole payload worth a one-line note confirming it was captured and will be resent on
    a later turn.
    """
    opaque_count = sum(
        1 for entry in entries
        if not isinstance(entry.get("text"), str) and not isinstance(entry.get("summary"), str)
    )
    if opaque_count == 0:
        return None
    noun = "block" if len(entries) == 1 else "blocks"
    return f"[{len(entries)} reasoning {noun} preserved, {opaque_count} encrypted]"


_WORKSPACE_PATH_DISPLAY_MAX_CHARS = 40


def format_workspace_path(
    path: Path, max_chars: int = _WORKSPACE_PATH_DISPLAY_MAX_CHARS,
) -> str:
    """Render `path` for the header: the full path if it's at most `max_chars` characters long,
    else just its last two components prefixed with `"..."` (e.g. `".../last/two_parts"`).
    """
    full = str(path)
    if len(full) <= max_chars or len(path.parts) < 2:
        return full
    return ".../" + "/".join(path.parts[-2:])


ANIMATION_TICK_SECONDS = 0.09
"""Interval between "still working" pulse-animation frames (seconds) -- see
`crawl_animation_text`. Shared by `RunningToolCallStatic` and `GettingReadyStatic`, which each
drive their own `Widget.set_interval` timer at this rate."""

_SWEEP_IDLE_SECONDS = 0.75
"""How long `crawl_animation_text` holds at default (unhighlighted) style between one
left-to-right sweep and the next."""


def _sweep_ticks(word: str) -> int:
    """Number of ticks one left-to-right sweep of `word` takes: enough for the bright cursor to
    advance from the first character until it has cleared the last character."""
    return len(word)


def _idle_ticks() -> int:
    """Number of ticks `crawl_animation_text` holds at default style between sweeps, i.e.
    `_SWEEP_IDLE_SECONDS` converted to a tick count at `ANIMATION_TICK_SECONDS` per tick."""
    return round(_SWEEP_IDLE_SECONDS / ANIMATION_TICK_SECONDS)


def crawl_animation_text(word: str, position: int) -> Text:
    """One frame of the "still working" pulse animation: a bright_white cursor sweeps left
    to right across `word` one character per tick, starting at the first character and
    continuing until it has swept past the last character and off the
    right edge; `word` then sits at default style, unhighlighted, for `_idle_ticks()` ticks
    before the sweep restarts. `position` is the caller's tick counter, incremented once per
    `ANIMATION_TICK_SECONDS` interval -- e.g. `RunningToolCallStatic`'s "Running..." while a
    tool call executes, or `GettingReadyStatic`'s "Getting ready..." while the first-turn
    session-naming classifier call is in flight.
    """
    sweep_ticks = _sweep_ticks(word)
    tick = position % (sweep_ticks + _idle_ticks())

    text = Text()
    if tick >= sweep_ticks:
        text.append(word)
        return text

    bright_pos = tick
    for i, ch in enumerate(word):
        if i == bright_pos:
            style = "bright_white"
        else:
            style = ""
        text.append(ch, style=style)
    return text


def pinned_to_bottom(history: VerticalScroll) -> bool:
    """Whether `history`'s viewport is currently showing its bottom edge, i.e. whether the
    user hasn't scrolled away from the latest content.

    Only `ReplApp._on_history_scroll_changed` should call this: it's accurate exactly when
    `history`'s `scroll_y` has just changed (mounting a widget doesn't itself update
    `max_scroll_y` until the next layout refresh, so calling this at an arbitrary other time
    can read a stale comparison). Everywhere else that needs to know whether to follow new
    content to the bottom reads the cached `ReplApp._history_pinned_to_bottom` instead.
    """
    return history.is_vertical_scroll_end


_DIFF_ADD_STYLE = "green"
_DIFF_DEL_STYLE = "red"
_DIFF_MUTED_STYLE = "dim"
_DIFF_LINE_MARKERS = {"add": "+", "del": "-", "context": " "}
_TRUNCATION_MARKER = "..."
"""Trailing, gutter-less line appended by every preview renderer below when its content was cut
short -- the same convention for a truncated diff preview and a truncated read preview."""


def _diff_gutter_width(hunks: list[DiffHunk]) -> int:
    """The number of characters needed to right-align the largest old/new line number appearing
    anywhere in `hunks`, so every gutter column in the rendered diff lines up."""
    max_lineno = 1
    for hunk in hunks:
        for line in hunk.lines:
            if line.old_lineno is not None:
                max_lineno = max(max_lineno, line.old_lineno)
            if line.new_lineno is not None:
                max_lineno = max(max_lineno, line.new_lineno)
    return len(str(max_lineno))


_COMPACT_CONTEXT_BEFORE_LINES = 2
"""How many lines of unchanged context `render_diff_content`'s compact (`max_lines`-capped) view
keeps immediately before the first changed line -- deliberately much less than the full
`DIFF_CONTEXT_LINES` a hunk actually carries (and shows in full in the uncapped/overlay view):
spending most of an already-short `max_lines` budget on leading context that isn't the change
itself would otherwise crowd the actual edit out of the compact preview -- or, for a change with
a full context window ahead of it, out entirely, leaving a compact view that shows nothing but
context and a trailing `"..."`."""


def render_diff_content(hunks: list[DiffHunk], *, max_lines: int | None) -> Content:
    """Render `hunks` (see `klorb.tools.util.diff_lines.build_diff_hunks`) as a `Content` with a
    right-aligned `old_lineno new_lineno` gutter, a `+`/`-`/` ` marker, and the line's text --
    `add` lines styled `green`, `del` lines `red`, `context` lines unstyled. A muted `"⋮"`
    separator line is inserted between two hunks that aren't adjacent (i.e. whenever there's more
    than one hunk at all -- `build_diff_hunks` only ever splits into multiple hunks when they're
    too far apart to share one another's context).

    `max_lines` caps how many diff lines (not counting the `"⋮"` separator) are rendered before
    appending a gutter-less `"..."` line and stopping -- `8` for the inline preview, `None`
    (uncapped) for the full-diff detail/overlay view, which is already naturally bounded by
    however much context `build_diff_hunks` kept on each side. When capped, rendering also starts
    `_COMPACT_CONTEXT_BEFORE_LINES` lines before the first changed line rather than at the very
    start of the first hunk's own (much longer) leading context -- otherwise, for a change with a
    full context window ahead of it, an 8-line cap would show nothing but that context-before and
    never reach the change itself. The full leading context is still there for the uncapped view.
    """
    width = _diff_gutter_width(hunks)
    flat: list[DiffLine | None] = []
    for hunk_index, hunk in enumerate(hunks):
        if hunk_index > 0:
            flat.append(None)  # hunk separator ("⋮")
        flat.extend(hunk.lines)

    if max_lines is not None:
        first_change = next(
            (i for i, entry in enumerate(flat) if entry is not None and entry.kind != "context"),
            0)
        flat = flat[max(0, first_change - _COMPACT_CONTEXT_BEFORE_LINES):]

    lines: list[str | tuple[str, str]] = []
    rendered = 0
    truncated = False
    for entry in flat:
        if max_lines is not None and rendered >= max_lines:
            truncated = True
            break
        if entry is None:
            lines.append(("⋮", _DIFF_MUTED_STYLE))
            continue
        old_str = str(entry.old_lineno) if entry.old_lineno is not None else ""
        new_str = str(entry.new_lineno) if entry.new_lineno is not None else ""
        marker = _DIFF_LINE_MARKERS[entry.kind]
        text = f"{old_str:>{width}} {new_str:>{width}} {marker} {entry.text}"
        if entry.kind == "add":
            lines.append((text, _DIFF_ADD_STYLE))
        elif entry.kind == "del":
            lines.append((text, _DIFF_DEL_STYLE))
        else:
            lines.append(text)
        rendered += 1
    if truncated:
        lines.append((_TRUNCATION_MARKER, _DIFF_MUTED_STYLE))
    return _assemble_lines(lines)


def render_read_preview_content(preview_lines: list[tuple[int, str]], truncated: bool) -> Content:
    """Render a `ReadPreview.preview_lines` (already capped to the inline preview's line count by
    the `Tool.read_preview()` override that built it) as a plain, numbered-gutter `Content`, with
    a trailing gutter-less `"..."` line when `truncated` -- the read-tool counterpart to
    `render_diff_content`'s inline (capped) form, but with no add/delete coloring since a read
    has nothing to compare against."""
    return _render_numbered_lines(preview_lines, truncated=truncated)


def render_full_file_content(lines: list[tuple[int, str]]) -> Content:
    """Render every line of a `FullFileView.lines` as a plain, numbered-gutter `Content` -- the
    full-file counterpart to `render_read_preview_content`, for the read click-to-expand
    overlay. Never truncated: this is the whole subject."""
    return _render_numbered_lines(lines, truncated=False)


def prefix_with_header(header: str, content: Content) -> Content:
    """Prepend `header` as its own plain (unstyled -- matching how `Tool.summary()`'s one-line
    text has always rendered) line above `content`, e.g. `"Edit file: foo.py (+1/-1)"` above the
    diff itself, or `"Read file: foo.py (lines 1-165 of 165)"` above the numbered preview --
    used for a `DiffPreview`/`ReadPreview`'s inline `summary_content`/`detail_content` in the
    history scroll, which otherwise show only the diff/content with no indication of which call
    or file they belong to. Not used for the click-to-expand overlay's body: `DiffDetailScreen`/
    `ReadDetailScreen` already show the label as their own separate header `Static`, so prefixing
    it into their content too would show it twice."""
    return Content.assemble(header, "\n", content)


def _render_numbered_lines(pairs: list[tuple[int, str]], *, truncated: bool) -> Content:
    width = len(str(pairs[-1][0])) if pairs else 1
    lines: list[str | tuple[str, str]] = [f"{lineno:>{width}} {text}" for lineno, text in pairs]
    if truncated:
        lines.append((_TRUNCATION_MARKER, _DIFF_MUTED_STYLE))
    return _assemble_lines(lines)


def _assemble_lines(lines: list[str | tuple[str, str]]) -> Content:
    """Join `lines` (each a plain string or a `(text, style)` pair, one per rendered row) into a
    single `Content` with a `"\\n"` between every pair of rows -- shared by every preview
    renderer above so none of them has to reason about trailing-newline bookkeeping itself."""
    if not lines:
        return Content("")
    parts: list[str | tuple[str, str]] = [lines[0]]
    for line in lines[1:]:
        parts.append("\n")
        parts.append(line)
    return Content.assemble(*parts)
