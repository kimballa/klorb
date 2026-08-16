# © Copyright 2026 Aaron Kimball
"""Tests for klorb.search_index.chunkers.markdown."""

from klorb.search_index.chunkers.markdown import MarkdownChunker

_DOC = """# Title

Intro paragraph one.

Intro paragraph two.

## Section A

Some content in section A.

## Section B

More content here.
"""


def test_empty_file_produces_no_chunks() -> None:
    assert MarkdownChunker().chunk("empty.md", "") == []


def test_splits_on_headings() -> None:
    chunks = MarkdownChunker().chunk("doc.md", _DOC)

    assert [chunk.start_line for chunk in chunks] == [1, 7, 11]
    assert chunks[1].text.startswith("## Section A")
    assert chunks[2].text.startswith("## Section B")


def test_file_with_no_headings_is_one_section() -> None:
    text = "just some text\n\nwith a paragraph break\n"

    chunks = MarkdownChunker().chunk("doc.md", text)

    assert len(chunks) == 1
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 3


def test_oversized_section_splits_into_paragraphs() -> None:
    long_paragraph = " ".join(["word"] * 500)
    text = f"# Title\n\n{long_paragraph}\n\nAnother short paragraph.\n"

    chunks = MarkdownChunker().chunk("doc.md", text)

    assert len(chunks) > 1
    assert all(chunk.kind == "markdown_section" for chunk in chunks)
