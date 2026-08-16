# © Copyright 2026 Aaron Kimball
"""`TreeSitterChunker`: the tree-sitter-driven structural chunker shared by `code_python.py` and
`code_typescript.py`, parameterized per language by a `LanguageSpec` rather than subclassed --
the two languages' grammars differ in node-type names, but the walk (top-level declarations,
class members, export wrappers, leftover-statement runs) is the same shape for both. A file that
fails to parse (tree-sitter reports an error node covering the whole tree) contributes no
structural chunks at all; the windowed chunker still covers it.
"""

import threading
from dataclasses import dataclass, field

from tree_sitter import Language, Node, Parser

from klorb.search_index.chunk import Chunk, ChunkKind
from klorb.search_index.chunkers.base import CATALOG, Chunker


@dataclass(frozen=True)
class LanguageSpec:
    language: Language
    class_types: frozenset[str]
    function_types: frozenset[str]
    method_types: frozenset[str]
    field_types: frozenset[str]
    """Node types within a class body, other than `method_types`, whose verbatim text is folded
    into the class's synopsis chunk -- Python assignment statements and its docstring alike (both
    are `expression_statement` nodes), and TS field declarations."""
    export_wrapper_types: frozenset[str] = field(default_factory=frozenset)
    """Node types (e.g. TypeScript's `export_statement`) that wrap a single real declaration as
    their sole named child -- unwrapped to classify the declaration, but the *outer* wrapper's
    span is still what gets chunked, so `export`/`export default` stays in the chunk's text."""
    class_body_field: str = "body"
    def_body_field: str = "body"


def _effective_node(node: Node, spec: LanguageSpec) -> Node:
    """`node` itself, or -- if `node` is an export/decorator wrapper -- its wrapped declaration
    (the first named child that isn't itself a `decorator`), used only to classify `node`; the
    wrapper's own span is still what gets chunked, so a decorator or `export` keyword stays in
    the chunk's text."""
    if node.type not in spec.export_wrapper_types:
        return node
    for candidate in node.named_children:
        if candidate.type != "decorator":
            return candidate
    return node


def _signature_text(outer: Node, effective: Node, body_field: str, source: bytes) -> str:
    """`outer`'s text (decorator included, if `outer` wraps `effective`) up to but not including
    `effective`'s body block -- the `def`/method signature line(s) without the implementation."""
    body = effective.child_by_field_name(body_field)
    end = body.start_byte if body is not None else outer.end_byte
    return source[outer.start_byte:end].decode("utf-8", errors="replace").rstrip()


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _line_span(node: Node) -> tuple[int, int]:
    return node.start_point.row + 1, node.end_point.row + 1


class TreeSitterChunker(Chunker):
    """A `tree_sitter.Parser` isn't safe for concurrent use, so each thread that calls `chunk()`
    gets its own lazily-constructed instance (`self._local`) rather than one shared per
    `TreeSitterChunker` -- the router that owns this chunker is itself a single shared instance
    across every thread of a multi-threaded `klorb index scan`."""

    def __init__(self, spec: LanguageSpec) -> None:
        self._spec = spec
        self._local = threading.local()

    def _parser(self) -> Parser:
        parser = getattr(self._local, "parser", None)
        if parser is None:
            parser = Parser(self._spec.language)
            self._local.parser = parser
        return parser

    def chunk(self, source_path: str, text: str) -> list[Chunk]:
        source = text.encode("utf-8")
        tree = self._parser().parse(source)
        root = tree.root_node
        if root.has_error and not root.named_children:
            return []
        chunks: list[Chunk] = []
        leftover_run: list[Node] = []

        def flush_leftover() -> None:
            if leftover_run:
                chunks.append(self._toplevel_chunk(leftover_run, source_path, source))
                leftover_run.clear()

        for child in root.named_children:
            effective = _effective_node(child, self._spec)
            if effective.type in self._spec.class_types:
                flush_leftover()
                chunks.append(self._class_chunk(child, effective, source_path, source))
                chunks.extend(self._method_chunks(effective, source_path, source))
            elif effective.type in self._spec.function_types:
                flush_leftover()
                chunks.append(self._toplevel_chunk([child], source_path, source, kind="function"))
            else:
                leftover_run.append(child)
        flush_leftover()
        return chunks

    def _class_chunk(self, outer: Node, class_node: Node, source_path: str, source: bytes) -> Chunk:
        header = _signature_text(outer, class_node, self._spec.class_body_field, source)
        body = class_node.child_by_field_name(self._spec.class_body_field)
        pieces = [header]
        if body is not None:
            for member in body.named_children:
                effective_member = _effective_node(member, self._spec)
                if effective_member.type in self._spec.method_types:
                    pieces.append(_signature_text(
                        member, effective_member, self._spec.def_body_field, source))
                elif effective_member.type in self._spec.field_types:
                    pieces.append(_node_text(member, source))
        start_line, end_line = _line_span(outer)
        return Chunk.create(
            catalog=CATALOG, source_path=source_path, kind="class",
            start_line=start_line, end_line=end_line, text="\n".join(pieces))

    def _method_chunks(self, class_node: Node, source_path: str, source: bytes) -> list[Chunk]:
        body = class_node.child_by_field_name(self._spec.class_body_field)
        if body is None:
            return []
        methods = []
        for member in body.named_children:
            if _effective_node(member, self._spec).type in self._spec.method_types:
                start_line, end_line = _line_span(member)
                methods.append(Chunk.create(
                    catalog=CATALOG, source_path=source_path, kind="method",
                    start_line=start_line, end_line=end_line, text=_node_text(member, source)))
        return methods

    def _toplevel_chunk(
        self, nodes: list[Node], source_path: str, source: bytes, *, kind: ChunkKind = "toplevel",
    ) -> Chunk:
        start_line = nodes[0].start_point.row + 1
        end_line = nodes[-1].end_point.row + 1
        chunk_text = source[nodes[0].start_byte:nodes[-1].end_byte].decode("utf-8", errors="replace")
        return Chunk.create(
            catalog=CATALOG, source_path=source_path, kind=kind,
            start_line=start_line, end_line=end_line, text=chunk_text)
