# © Copyright 2026 Aaron Kimball
"""`SemanticSearchCore`: shared mechanics behind `SemanticSearch`/`SearchMemories`'s semantic-hit
handling. See docs/specs/local-search-index.md.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from klorb.search_index.chunk import Chunk
from klorb.tools.util.search_core import format_match_line
from klorb.tools.util.secret_redaction import SecretRedactor

if TYPE_CHECKING:
    from klorb.session import Session

_TRUNCATION_SUFFIX = "[truncated...]"


class HybridSearchable(Protocol):
    """Structural type for anything `SemanticSearchCore.merged_hits` can query."""

    def hybrid_search(self, query_text: str, limit: int, catalog: str) -> list[tuple[Chunk, float]]: ...


def _truncate_line(line: str, max_length: int) -> str:
    if len(line) <= max_length:
        return line
    return line[:max_length] + _TRUNCATION_SUFFIX


class SemanticSearchCore:
    """Constructed once per tool call with the rendering settings that call needs."""

    def __init__(self, *, max_line_length: int, secret_redactor: SecretRedactor) -> None:
        self._max_line_length = max_line_length
        self._secret_redactor = secret_redactor

    def merged_hits(
        self, indexers: list[tuple[HybridSearchable, str]], queries: list[str], *,
        limit_per_query: int, min_score: float = 0.0,
    ) -> list[tuple[Chunk, float]]:
        """Run every query through every `(indexer, catalog)` pair's `hybrid_search`,
        deduplicating by chunk id and keeping each chunk's highest score, dropping any hit
        scoring below `min_score`."""
        best_by_chunk_id: dict[str, tuple[Chunk, float]] = {}
        for indexer, catalog in indexers:
            for query in queries:
                for chunk, score in indexer.hybrid_search(query, limit_per_query, catalog):
                    if score < min_score:
                        continue
                    existing = best_by_chunk_id.get(chunk.chunk_id)
                    if existing is None or score > existing[1]:
                        best_by_chunk_id[chunk.chunk_id] = (chunk, score)
        return list(best_by_chunk_id.values())

    def render_chunk_lines(
        self, session: "Session | None", abs_path: Path, chunk: Chunk,
    ) -> list[str] | None:
        """`chunk`'s line span rendered in dense format, every line marked matched, or `None` if
        `abs_path` can no longer be read. Reads `abs_path` from disk rather than `chunk.text`,
        since a structural chunk's text is a synthesized synopsis, not necessarily the file's
        real content over that span."""
        try:
            text = abs_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        all_lines = text.splitlines()
        start = max(0, chunk.start_line - 1)
        end = min(len(all_lines), chunk.end_line)
        dense_lines = [format_match_line(i + 1, all_lines[i], matched=True) for i in range(start, end)]
        redacted = [self._secret_redactor.redact(session, line) for line in dense_lines]
        return [_truncate_line(line, self._max_line_length) for line in redacted]
