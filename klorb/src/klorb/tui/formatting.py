# © Copyright 2026 Aaron Kimball
"""Pure formatting/data-munging helpers shared across `klorb.tui` modules: no widget state,
no `self`, just a value in and a value out.
"""

import importlib.resources
import json
import random
import re
from pathlib import Path
from typing import Any

from rich.text import Text
from textual.containers import VerticalScroll
from textual.content import Content

from klorb.tools.util.diff_lines import DiffHunk, DiffLine


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


def _readable_entry_text(entry: dict[str, Any]) -> str | None:
    """One `reasoning_details` entry's own human-readable text, if it has one."""
    text = entry.get("text")
    if isinstance(text, str):
        return text
    summary = entry.get("summary")
    return summary if isinstance(summary, str) else None


def resolve_thinking_body_text(content: str, reasoning_details: list[dict[str, Any]] | None) -> str:
    """The text a `<Thinking>` block should actually show: `content` (the model's plain-text
    `reasoning` delta) if it has anything, else every `reasoning_details` entry's own `text`/
    `summary` field joined together. A provider can stream reasoning *only* via structured
    `reasoning_details` fragments, with no matching plain-text `reasoning` delta ever sent.
    Returns `content` unchanged (including empty) when there's nothing in `reasoning_details`
    to fall back to.
    """
    if content.strip():
        return content
    if not reasoning_details:
        return content
    readable = list(filter(None, map(_readable_entry_text, reasoning_details)))
    return "\n\n".join(readable) if readable else content


def summarize_reasoning_details(entries: list[dict[str, Any]]) -> str | None:
    """Return a compact indicator string for a turn's `reasoning_details` payload, or `None` if
    every entry carries its own human-readable `text`/`summary` string. An entry with neither
    field carries information the `<Thinking>` block can't display, so any such entry makes
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
    else just its last two components prefixed with `"..."`.
    """
    full = str(path)
    if len(full) <= max_chars or len(path.parts) < 2:
        return full
    return ".../" + "/".join(path.parts[-2:])


ANIMATION_TICK_SECONDS = 0.09
"""Interval between "still working" pulse-animation frames (seconds)."""

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
    continuing until it has swept past the last character and off the right edge; `word` then
    sits at default style, unhighlighted, for `_idle_ticks()` ticks before the sweep restarts.
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
    user hasn't scrolled away from the latest content."""
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
keeps immediately before the first changed line: spending most of an already-short `max_lines`
budget on leading context that isn't the change itself would otherwise crowd the actual edit
out of the compact preview."""


def render_diff_content(hunks: list[DiffHunk], *, max_lines: int | None) -> Content:
    """Render `hunks` as a `Content` with a right-aligned `old_lineno new_lineno` gutter, a
    `+`/`-`/` ` marker, and the line's text. A muted `"⋮"` separator line is inserted between
    two hunks that aren't adjacent.

    `max_lines` caps how many diff lines (not counting the `"⋮"` separator) are rendered before
    appending a gutter-less `"..."` line and stopping. When capped, rendering also starts
    `_COMPACT_CONTEXT_BEFORE_LINES` lines before the first changed line rather than at the very
    start of the first hunk's own (much longer) leading context. The full leading context is
    still there for the uncapped view.
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
    """Render `ReadPreview.preview_lines` as a plain, numbered-gutter `Content`, with a trailing
    gutter-less `"..."` line when `truncated`.
    """
    return _render_numbered_lines(preview_lines, truncated=truncated)


def render_full_file_content(lines: list[tuple[int, str]]) -> Content:
    """Render every line of a `FullFileView.lines` as a plain, numbered-gutter `Content`.
    Never truncated: this is the whole subject."""
    return _render_numbered_lines(lines, truncated=False)


def prefix_with_header(header: str, content: Content) -> Content:
    """Prepend `header` as its own plain line above `content`."""
    return Content.assemble(header, "\n", content)


def _render_numbered_lines(pairs: list[tuple[int, str]], *, truncated: bool) -> Content:
    width = len(str(pairs[-1][0])) if pairs else 1
    lines: list[str | tuple[str, str]] = [f"{lineno:>{width}} {text}" for lineno, text in pairs]
    if truncated:
        lines.append((_TRUNCATION_MARKER, _DIFF_MUTED_STYLE))
    return _assemble_lines(lines)


def _assemble_lines(lines: list[str | tuple[str, str]]) -> Content:
    """Join `lines` (each a plain string or a `(text, style)` pair, one per rendered row) into a
    single `Content` with a `"\\n"` between every pair of rows."""
    if not lines:
        return Content("")
    parts: list[str | tuple[str, str]] = [lines[0]]
    for line in lines[1:]:
        parts.append("\n")
        parts.append(line)
    return Content.assemble(*parts)


_SYSTEM_INTERJECTION_RE = re.compile(
    r"<SystemInterjection\s+subject=\"[^\"]*\">\n.*?\n</SystemInterjection>\n?",
    re.DOTALL,
)
"""Matches a `<SystemInterjection subject="...">` block (opening tag, any content,
closing tag) including the trailing newline, so stripping leaves no orphan blank lines."""


def strip_system_interjections(text: str) -> str:
    """Remove every `<SystemInterjection>` block from `text`, collapsing any resulting
    runs of blank lines down to at most one, and stripping leading/trailing whitespace."""
    cleaned = _SYSTEM_INTERJECTION_RE.sub("", text)
    # Collapse runs of blank lines (including the gaps left by removed blocks).
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


_USER_SKILL_ACTIVATION_RE = re.compile(
    r"<SystemInterjection\s+subject=\"UserSkillActivation\">\n.*?\n(\{.*\})\n</SystemInterjection>",
    re.DOTALL,
)
"""Matches the one `UserSkillActivation` block `strip_system_interjections` would otherwise
elide entirely, capturing its JSON payload."""


def extract_skill_activation_notice(text: str) -> str | None:
    """If `text` carries a `UserSkillActivation` interjection, return a one-line "Activated skill:
    <namespace>/<name>" notice for it. `None` if `text` carries no such interjection, or its
    payload doesn't parse."""
    match = _USER_SKILL_ACTIVATION_RE.search(text)
    if match is None:
        return None
    try:
        payload = json.loads(match.group(1))
        return f"Activated skill: {payload['namespace']}/{payload['name']}"
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
