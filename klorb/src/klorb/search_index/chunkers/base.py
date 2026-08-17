# © Copyright 2026 Aaron Kimball
"""`Chunker`: the interface every per-file-type chunker implements."""

from abc import ABC, abstractmethod

from klorb.search_index.chunk import Chunk

CATALOG = "workspace"
"""The default `Chunk.catalog` value, for workspace-file chunking -- see
docs/specs/local-search-index.md. The memories catalogs pass their own `catalog` explicitly."""


class Chunker(ABC):
    """Splits one file's `text` into indexable `Chunk`s tagged with `catalog`. `source_path` is
    workspace-root-relative with forward slashes, matching `Chunk.source_path`."""

    @abstractmethod
    def chunk(self, source_path: str, text: str, catalog: str = CATALOG) -> list[Chunk]: ...
