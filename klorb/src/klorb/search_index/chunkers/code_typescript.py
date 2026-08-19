# © Copyright 2026 Aaron Kimball
"""TypeScript/TSX structural chunker: one chunk per class, one chunk per method, one chunk per
top-level function, one chunk per contiguous run of leftover top-level statements.
"""

import tree_sitter_typescript
from tree_sitter import Language

from klorb.search_index.chunkers._tree_sitter_base import LanguageSpec, TreeSitterChunker

TYPESCRIPT_LANGUAGE = Language(tree_sitter_typescript.language_typescript())
TSX_LANGUAGE = Language(tree_sitter_typescript.language_tsx())

_CLASS_TYPES = frozenset({"class_declaration"})
_FUNCTION_TYPES = frozenset({"function_declaration"})
_METHOD_TYPES = frozenset({"method_definition"})
_FIELD_TYPES = frozenset({"public_field_definition"})
_EXPORT_TYPES = frozenset({"export_statement"})

_TS_SPEC = LanguageSpec(
    language=TYPESCRIPT_LANGUAGE, class_types=_CLASS_TYPES, function_types=_FUNCTION_TYPES,
    method_types=_METHOD_TYPES, field_types=_FIELD_TYPES, export_wrapper_types=_EXPORT_TYPES)
_TSX_SPEC = LanguageSpec(
    language=TSX_LANGUAGE, class_types=_CLASS_TYPES, function_types=_FUNCTION_TYPES,
    method_types=_METHOD_TYPES, field_types=_FIELD_TYPES, export_wrapper_types=_EXPORT_TYPES)


class TypeScriptChunker(TreeSitterChunker):
    def __init__(self) -> None:
        super().__init__(_TS_SPEC)


class TsxChunker(TreeSitterChunker):
    def __init__(self) -> None:
        super().__init__(_TSX_SPEC)
