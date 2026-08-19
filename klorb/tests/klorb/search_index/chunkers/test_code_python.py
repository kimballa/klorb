# © Copyright 2026 Aaron Kimball
"""Tests for klorb.search_index.chunkers.code_python."""

import threading

from klorb.search_index.chunkers.code_python import PythonChunker

_SOURCE = '''import os


class Foo:
    """A docstring."""

    x: int = 1

    def bar(self, y: int) -> int:
        """Adds one."""
        return y + 1

    @staticmethod
    def baz() -> None:
        pass


def toplevel_fn():
    return 42


CONST = 99
'''


def test_empty_file_produces_no_chunks() -> None:
    assert PythonChunker().chunk("empty.py", "") == []


def test_produces_one_chunk_per_class_method_function_and_leftover_run() -> None:
    chunks = PythonChunker().chunk("foo.py", _SOURCE)

    kinds = [chunk.kind for chunk in chunks]
    assert kinds == ["toplevel", "class", "method", "method", "function", "toplevel"]


def test_class_chunk_includes_docstring_field_and_method_signatures_not_bodies() -> None:
    chunks = PythonChunker().chunk("foo.py", _SOURCE)
    class_chunk = next(chunk for chunk in chunks if chunk.kind == "class")

    assert "class Foo:" in class_chunk.text
    assert "A docstring." in class_chunk.text
    assert "x: int = 1" in class_chunk.text
    assert "def bar(self, y: int) -> int:" in class_chunk.text
    assert "return y + 1" not in class_chunk.text


def test_decorated_method_is_still_captured() -> None:
    chunks = PythonChunker().chunk("foo.py", _SOURCE)
    method_chunks = [chunk for chunk in chunks if chunk.kind == "method"]

    assert any("baz" in chunk.text for chunk in method_chunks)
    baz_chunk = next(chunk for chunk in method_chunks if "baz" in chunk.text)
    assert "@staticmethod" in baz_chunk.text


def test_function_chunk_has_full_body() -> None:
    chunks = PythonChunker().chunk("foo.py", _SOURCE)
    function_chunk = next(chunk for chunk in chunks if chunk.kind == "function")

    assert "def toplevel_fn():" in function_chunk.text
    assert "return 42" in function_chunk.text


def test_garbage_input_produces_no_class_or_function_chunks() -> None:
    # tree-sitter is error-tolerant, so garbage input still parses into a module with content.
    chunks = PythonChunker().chunk("broken.py", "\x00\x01 not python at all {{{")

    assert all(chunk.kind == "toplevel" for chunk in chunks)


def test_completely_empty_of_named_nodes_produces_no_chunks() -> None:
    assert PythonChunker().chunk("blank.py", "   \n\t\n") == []


def test_chunk_is_safe_across_concurrent_threads() -> None:
    # Regression test: a shared tree_sitter.Parser isn't safe for concurrent .parse() calls.
    chunker = PythonChunker()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            for _ in range(20):
                chunks = chunker.chunk("foo.py", _SOURCE)
                assert [chunk.kind for chunk in chunks] == [
                    "toplevel", "class", "method", "method", "function", "toplevel"]
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
