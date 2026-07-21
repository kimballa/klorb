# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.formatting's pure helper functions."""

from klorb.tools.util import build_diff_hunks
from klorb.tui.formatting import (
    _idle_ticks,
    _sweep_ticks,
    crawl_animation_text,
    format_token_count,
    render_diff_content,
    summarize_reasoning_details,
)


def test_format_token_count_examples() -> None:
    assert format_token_count(0) == "0"
    assert format_token_count(500) == "500"
    assert format_token_count(999) == "999"
    assert format_token_count(1000) == "1k"
    assert format_token_count(1400) == "1.4k"
    assert format_token_count(23000) == "23k"
    assert format_token_count(24000) == "24k"
    assert format_token_count(25000) == "25k"
    assert format_token_count(200000) == "200k"
    assert format_token_count(210000) == "210k"
    assert format_token_count(220000) == "220k"
    assert format_token_count(423000) == "420k"
    assert format_token_count(1_000_000) == "1M"


def test_summarize_reasoning_details_is_none_when_every_entry_has_text() -> None:
    assert summarize_reasoning_details([
        {"type": "reasoning.text", "text": "Let me think."},
        {"type": "reasoning.summary", "summary": "Short summary."},
    ]) is None


def test_summarize_reasoning_details_notes_encrypted_entries() -> None:
    result = summarize_reasoning_details([
        {"type": "reasoning.text", "text": "visible"},
        {"type": "reasoning.encrypted", "data": "opaque-blob"},
    ])

    assert result == "[2 reasoning blocks preserved, 1 encrypted]"


def test_summarize_reasoning_details_singular_block_wording() -> None:
    result = summarize_reasoning_details([{"type": "reasoning.encrypted", "data": "opaque-blob"}])

    assert result == "[1 reasoning block preserved, 1 encrypted]"


def _styles(word: str, position: int) -> list[str]:
    text = crawl_animation_text(word, position)
    styles = [""] * len(word)
    for span in text.spans:
        styles[span.start] = str(span.style)
    return styles


def test_crawl_animation_text_starts_at_left_edge() -> None:
    # Tick 0 of a sweep: bright cursor at index 0.
    styles = _styles("Running", 0)
    assert styles[0] == "bright_white"
    assert all(s == "" for i, s in enumerate(styles) if i != 0)


def test_crawl_animation_text_sweep_advances_one_char_per_tick() -> None:
    word = "Running"
    for tick in range(_sweep_ticks(word)):
        styles = _styles(word, tick)
        bright_indices = [i for i, s in enumerate(styles) if s == "bright_white"]
        assert bright_indices in ([], [tick])


def test_crawl_animation_text_idle_after_sweep_has_no_highlighting() -> None:
    word = "Running"
    sweep_ticks = _sweep_ticks(word)
    idle_ticks = _idle_ticks()
    assert idle_ticks > 0
    for offset in range(idle_ticks):
        styles = _styles(word, sweep_ticks + offset)
        assert all(s == "" for s in styles)


def test_crawl_animation_text_cycle_repeats() -> None:
    word = "Running"
    cycle_length = _sweep_ticks(word) + _idle_ticks()
    for tick in range(cycle_length):
        assert _styles(word, tick) == _styles(word, tick + cycle_length)


def test_render_diff_content_full_view_shows_the_whole_leading_context() -> None:
    old_lines = [str(i) for i in range(1, 31)]
    new_lines = list(old_lines)
    new_lines[14] = "X"  # 0-indexed -- changes line 15
    hunks = build_diff_hunks(old_lines, new_lines)

    full = str(render_diff_content(hunks, max_lines=None))

    # 1 context/del/add line per source line: 8 lines of context before (7-14), the del/add
    # pair for line 15, and 8 lines of context after (16-23) -- no truncation.
    assert full.count("\n") + 1 == 18
    assert "..." not in full


def test_render_diff_content_compact_view_starts_two_lines_before_the_change() -> None:
    """A change with a full (8-line) leading context ahead of it must not have its `max_lines=8`
    compact preview spent entirely on that context: it should start close to the change instead,
    so the change itself is always visible in the compact preview -- see
    docs/specs/terminal-repl.md's "Diff and read previews" section."""
    old_lines = [str(i) for i in range(1, 31)]
    new_lines = list(old_lines)
    new_lines[14] = "X"
    hunks = build_diff_hunks(old_lines, new_lines)

    compact = str(render_diff_content(hunks, max_lines=8))

    # 2 lines of context before (13, 14), the del/add pair for line 15, and as much trailing
    # context as fits in the remaining budget (16-19) -- 8 lines total, then truncated.
    assert compact.count("\n") + 1 == 9
    assert "- 15" in compact
    assert "+ X" in compact
    assert compact.rstrip().endswith("...")
    # None of the far-out (untouched) context lines this hunk's full view would show leak in.
    assert " 7  7   7" not in compact


def test_render_diff_content_compact_view_clamps_at_the_start_of_the_hunk() -> None:
    """A change with less than 2 lines of leading context (here, none -- line 1 itself changes)
    must not go negative when backing up; it should simply start at the hunk's own beginning."""
    old_lines = ["a", "b", "c"]
    new_lines = ["A", "b", "c"]
    hunks = build_diff_hunks(old_lines, new_lines, context=8)

    compact = str(render_diff_content(hunks, max_lines=8))

    assert "- a" in compact
    assert "+ A" in compact
    assert "..." not in compact
