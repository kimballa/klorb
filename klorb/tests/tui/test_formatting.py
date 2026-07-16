# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.formatting's pure helper functions."""

from klorb.tui.formatting import _summarize_reasoning_details, format_token_count


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
