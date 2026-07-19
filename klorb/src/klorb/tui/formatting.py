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

from klorb.permissions.directory_access import DirRules


def _concat_dir_rules(base: DirRules, addition: DirRules) -> DirRules:
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


def _random_greeting() -> str:
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


def _summarize_reasoning_details(entries: list[dict[str, Any]]) -> str | None:
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


def _format_workspace_path(
    path: Path, max_chars: int = _WORKSPACE_PATH_DISPLAY_MAX_CHARS,
) -> str:
    """Render `path` for the header: the full path if it's at most `max_chars` characters long,
    else just its last two components prefixed with `"..."` (e.g. `".../last/two_parts"`).
    """
    full = str(path)
    if len(full) <= max_chars or len(path.parts) < 2:
        return full
    return ".../" + "/".join(path.parts[-2:])


ANIMATION_TICK_SECONDS = 0.24
"""Interval between "still working" crawl-animation frames (seconds) -- see
`crawl_animation_text`. At 0.24s per frame and 9 characters in e.g. `"Running..."`, a full
crawl cycle takes just over two seconds -- slow enough to feel calm without being distracting.
Shared by `RunningToolCallStatic` and `GettingReadyStatic`, which each drive their own
`Widget.set_interval` timer at this rate."""


def crawl_animation_text(word: str, position: int) -> Text:
    """One frame of the "still working" crawl animation: `word` with the character at
    `position % len(word)` in `bright_white`, the character two positions behind it (wrapping
    around the start of `word`) in `dim`, and every other character in default style -- e.g.
    `RunningToolCallStatic`'s "Running..." while a tool call executes, or
    `GettingReadyStatic`'s "Getting ready..." while the first-turn session-naming classifier
    call is in flight."""
    text = Text()
    pos = position % len(word)
    dim_pos = (pos - 2) % len(word)
    for i, ch in enumerate(word):
        if i == pos:
            style = "bright_white"
        elif i == dim_pos:
            style = "dim"
        else:
            style = ""
        text.append(ch, style=style)
    return text


def _pinned_to_bottom(history: VerticalScroll) -> bool:
    """Whether `history`'s viewport is currently showing its bottom edge, i.e. whether the
    user hasn't scrolled away from the latest content.

    Only `ReplApp._on_history_scroll_changed` should call this: it's accurate exactly when
    `history`'s `scroll_y` has just changed (mounting a widget doesn't itself update
    `max_scroll_y` until the next layout refresh, so calling this at an arbitrary other time
    can read a stale comparison). Everywhere else that needs to know whether to follow new
    content to the bottom reads the cached `ReplApp._history_pinned_to_bottom` instead.
    """
    return history.is_vertical_scroll_end
