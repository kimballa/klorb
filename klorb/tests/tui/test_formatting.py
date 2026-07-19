# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.formatting's pure helper functions."""

from klorb.tui.formatting import (
    _idle_ticks,
    _summarize_reasoning_details,
    _sweep_ticks,
    crawl_animation_text,
    format_token_count,
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
    assert _summarize_reasoning_details([
        {"type": "reasoning.text", "text": "Let me think."},
        {"type": "reasoning.summary", "summary": "Short summary."},
    ]) is None


def test_summarize_reasoning_details_notes_encrypted_entries() -> None:
    result = _summarize_reasoning_details([
        {"type": "reasoning.text", "text": "visible"},
        {"type": "reasoning.encrypted", "data": "opaque-blob"},
    ])

    assert result == "[2 reasoning blocks preserved, 1 encrypted]"


def test_summarize_reasoning_details_singular_block_wording() -> None:
    result = _summarize_reasoning_details([{"type": "reasoning.encrypted", "data": "opaque-blob"}])

    assert result == "[1 reasoning block preserved, 1 encrypted]"


def _styles(word: str, position: int) -> list[str]:
    text = crawl_animation_text(word, position)
    styles = [""] * len(word)
    for span in text.spans:
        styles[span.start] = str(span.style)
    return styles


def test_crawl_animation_text_starts_at_left_edge() -> None:
    # Tick 0 of a sweep: bright cursor at the first character, dim cursor not yet on-screen.
    styles = _styles("Running", 0)
    assert styles[0] == "bright_white"
    assert all(s == "" for s in styles[1:])


def test_crawl_animation_text_bright_leads_dim_by_two_chars() -> None:
    # "Running": R-u-n-n-i-n-g, bright at index 3 ('n'), dim two positions behind at index 1 ('u').
    styles = _styles("Running", 3)
    assert styles[3] == "bright_white"
    assert styles[1] == "dim"
    assert all(s == "" for i, s in enumerate(styles) if i not in (1, 3))


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
