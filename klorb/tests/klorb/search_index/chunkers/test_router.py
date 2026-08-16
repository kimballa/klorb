# © Copyright 2026 Aaron Kimball
"""Tests for klorb.search_index.chunkers.router."""

from klorb.search_index.chunkers.router import ChunkerRouter, get_chunker_router


def test_python_file_gets_structural_and_windowed_chunks() -> None:
    router = ChunkerRouter()
    text = "def foo():\n    return 1\n"

    chunks = router.chunk_file("foo.py", text)

    kinds = {chunk.kind for chunk in chunks}
    assert "function" in kinds
    assert "window" in kinds


def test_markdown_file_gets_markdown_and_windowed_chunks() -> None:
    router = ChunkerRouter()
    text = "# Title\n\nSome content.\n"

    chunks = router.chunk_file("doc.md", text)

    kinds = {chunk.kind for chunk in chunks}
    assert "markdown_section" in kinds
    assert "window" in kinds


def test_unrecognized_extension_gets_only_windowed_chunks() -> None:
    router = ChunkerRouter()
    text = "some,csv,data\n1,2,3\n"

    chunks = router.chunk_file("data.csv", text)

    assert chunks
    assert all(chunk.kind == "window" for chunk in chunks)


def test_get_chunker_router_returns_the_same_instance() -> None:
    assert get_chunker_router() is get_chunker_router()
