# © Copyright 2026 Aaron Kimball
"""`Chunk`: one indexable unit of workspace content -- a class body, a method, a markdown
section, a fixed-size line window -- as produced by a `klorb.search_index.chunkers.base.Chunker`
and stored by `klorb.search_index.store.SearchIndexStore`.
"""

import hashlib
from typing import Literal

from pydantic import BaseModel

from klorb.token_estimate import estimate_tokens

ChunkKind = Literal[
    "class", "method", "function", "toplevel", "markdown_section", "window",
]


class Chunk(BaseModel):
    """One indexed span of a workspace file: `start_line`/`end_line` are 1-indexed and
    inclusive, `source_path` is workspace-root-relative with forward slashes. `chunk_id`,
    `token_count`, and `content_hash` are always derived from the other fields via `create()`,
    never supplied independently, so two chunkers can never produce inconsistent derived values
    for the same span."""

    chunk_id: str
    catalog: str
    source_path: str
    kind: ChunkKind
    start_line: int
    end_line: int
    text: str
    token_count: int
    content_hash: str

    @classmethod
    def create(
        cls, *, catalog: str, source_path: str, kind: ChunkKind,
        start_line: int, end_line: int, text: str,
    ) -> "Chunk":
        """Build a `Chunk`, deriving `chunk_id` from `(catalog, source_path, kind, start_line,
        end_line)` -- stable across reindexing runs, so re-chunking an unchanged span upserts the
        same row rather than creating a duplicate -- and `token_count`/`content_hash` from
        `text`."""
        chunk_id = f"{catalog}:{source_path}:{kind}:{start_line}-{end_line}"
        content_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
        return cls(
            chunk_id=chunk_id, catalog=catalog, source_path=source_path, kind=kind,
            start_line=start_line, end_line=end_line, text=text,
            token_count=estimate_tokens(text), content_hash=content_hash,
        )
