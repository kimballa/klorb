# © Copyright 2026 Aaron Kimball
"""Tests for klorb.search_index.chunkers.code_typescript."""

from klorb.search_index.chunkers.code_typescript import TypeScriptChunker

_SOURCE = '''import { foo } from "./foo";

export class Widget {
  private count: number = 0;

  increment(): number {
    this.count += 1;
    return this.count;
  }
}

export function helper(): void {
  console.log("hi");
}

const CONST = 42;
'''


def test_empty_file_produces_no_chunks() -> None:
    assert TypeScriptChunker().chunk("empty.ts", "") == []


def test_produces_one_chunk_per_class_method_function_and_leftover_run() -> None:
    chunks = TypeScriptChunker().chunk("widget.ts", _SOURCE)

    kinds = [chunk.kind for chunk in chunks]
    assert kinds == ["toplevel", "class", "method", "function", "toplevel"]


def test_export_keyword_stays_in_the_class_and_function_chunk_text() -> None:
    chunks = TypeScriptChunker().chunk("widget.ts", _SOURCE)
    class_chunk = next(chunk for chunk in chunks if chunk.kind == "class")
    function_chunk = next(chunk for chunk in chunks if chunk.kind == "function")

    assert class_chunk.text.startswith("export class Widget")
    assert function_chunk.text.startswith("export function helper")


def test_class_chunk_includes_field_and_method_signature_not_body() -> None:
    chunks = TypeScriptChunker().chunk("widget.ts", _SOURCE)
    class_chunk = next(chunk for chunk in chunks if chunk.kind == "class")

    assert "private count: number = 0" in class_chunk.text
    assert "increment(): number" in class_chunk.text
    assert "this.count += 1" not in class_chunk.text


def test_method_chunk_has_full_body() -> None:
    chunks = TypeScriptChunker().chunk("widget.ts", _SOURCE)
    method_chunk = next(chunk for chunk in chunks if chunk.kind == "method")

    assert "this.count += 1" in method_chunk.text
