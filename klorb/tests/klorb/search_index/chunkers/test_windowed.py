# © Copyright 2026 Aaron Kimball
"""Tests for klorb.search_index.chunkers.windowed."""

import pytest

from klorb.search_index.chunkers.windowed import WindowedChunker


def test_empty_file_produces_no_chunks() -> None:
    assert WindowedChunker().chunk("empty.txt", "") == []


def test_short_file_produces_one_window_covering_the_whole_file() -> None:
    text = "\n".join(f"line{i}" for i in range(1, 11))

    chunks = WindowedChunker(window_lines=60, overlap_lines=10).chunk("short.txt", text)

    assert len(chunks) == 1
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 10


def test_long_file_produces_overlapping_windows_covering_every_line() -> None:
    text = "\n".join(f"line{i}" for i in range(1, 151))

    chunks = WindowedChunker(window_lines=60, overlap_lines=10).chunk("long.txt", text)

    spans = [(chunk.start_line, chunk.end_line) for chunk in chunks]
    assert spans == [(1, 60), (51, 110), (101, 150)]


def test_overlap_must_be_smaller_than_window() -> None:
    with pytest.raises(ValueError, match="overlap_lines"):
        WindowedChunker(window_lines=10, overlap_lines=10)
