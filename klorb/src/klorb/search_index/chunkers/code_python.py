# © Copyright 2026 Aaron Kimball
"""Python structural chunker: one chunk per class (fields, method signatures, docstring), one
chunk per method, one chunk per top-level function, and one chunk per contiguous run of leftover
top-level statements.
"""

import tree_sitter_python
from tree_sitter import Language

from klorb.search_index.chunkers._tree_sitter_base import LanguageSpec, TreeSitterChunker

PYTHON_LANGUAGE = Language(tree_sitter_python.language())

_SPEC = LanguageSpec(
    language=PYTHON_LANGUAGE,
    class_types=frozenset({"class_definition"}),
    function_types=frozenset({"function_definition"}),
    method_types=frozenset({"function_definition"}),
    field_types=frozenset({"expression_statement"}),
    export_wrapper_types=frozenset({"decorated_definition"}),
)


class PythonChunker(TreeSitterChunker):
    def __init__(self) -> None:
        super().__init__(_SPEC)
