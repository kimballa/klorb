# © Copyright 2026 Aaron Kimball
"""Tests for klorb.search_index.chunk."""

from klorb.search_index.chunk import Chunk


def test_create_derives_chunk_id_from_identity_fields() -> None:
    chunk = Chunk.create(
        catalog="workspace", source_path="foo/bar.py", kind="function",
        start_line=1, end_line=5, text="def foo():\n    pass")

    assert chunk.chunk_id == "workspace:foo/bar.py:function:1-5"


def test_create_is_stable_across_calls_with_the_same_span() -> None:
    first = Chunk.create(
        catalog="workspace", source_path="foo.py", kind="toplevel",
        start_line=1, end_line=2, text="import os")
    second = Chunk.create(
        catalog="workspace", source_path="foo.py", kind="toplevel",
        start_line=1, end_line=2, text="import os")

    assert first.chunk_id == second.chunk_id
    assert first.content_hash == second.content_hash


def test_create_derives_different_content_hash_for_different_text() -> None:
    first = Chunk.create(
        catalog="workspace", source_path="foo.py", kind="toplevel",
        start_line=1, end_line=1, text="import os")
    second = Chunk.create(
        catalog="workspace", source_path="foo.py", kind="toplevel",
        start_line=1, end_line=1, text="import sys")

    assert first.content_hash != second.content_hash


def test_create_computes_token_count() -> None:
    chunk = Chunk.create(
        catalog="workspace", source_path="foo.py", kind="toplevel",
        start_line=1, end_line=1, text="import os")

    assert chunk.token_count > 0
