# © Copyright 2026 Aaron Kimball
"""`Chunker`: the interface every per-file-type chunker implements."""

from abc import ABC, abstractmethod

from klorb.search_index.chunk import Chunk

CATALOG = "workspace"
"""The default `Chunk.catalog` value, for workspace-file chunking."""


class Chunker(ABC):
    """Splits one file's `text` into indexable `Chunk`s tagged with `catalog`."""

    @abstractmethod
    def chunk(self, source_path: str, text: str, catalog: str = CATALOG) -> list[Chunk]: ...
