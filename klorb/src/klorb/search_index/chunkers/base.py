# © Copyright 2026 Aaron Kimball
"""`Chunker`: the interface every per-file-type chunker implements."""

from abc import ABC, abstractmethod

from klorb.search_index.chunk import Chunk

CATALOG = "workspace"
"""The sole `Chunk.catalog` value this MVP's chunkers produce -- see docs/specs/local-search-index.md."""


class Chunker(ABC):
    """Splits one file's `text` into indexable `Chunk`s. `source_path` is workspace-root-relative
    with forward slashes, matching `Chunk.source_path`."""

    @abstractmethod
    def chunk(self, source_path: str, text: str) -> list[Chunk]: ...
