# © Copyright 2026 Aaron Kimball
"""`WindowedChunker`: a fixed-size, overlapping sliding window over a file's raw lines,
independent of any structural or markdown boundaries -- a fallback recall net for content that
doesn't parse cleanly, or that spans structural boundaries a structural chunker splits apart.
Applied unconditionally alongside every other chunker.
"""

from klorb.search_index.chunk import Chunk
from klorb.search_index.chunkers.base import CATALOG, Chunker

DEFAULT_WINDOW_LINES = 60
DEFAULT_OVERLAP_LINES = 10


class WindowedChunker(Chunker):
    def __init__(
        self, *, window_lines: int = DEFAULT_WINDOW_LINES, overlap_lines: int = DEFAULT_OVERLAP_LINES,
    ) -> None:
        if overlap_lines >= window_lines:
            raise ValueError(f"overlap_lines ({overlap_lines}) must be < window_lines ({window_lines})")
        self._window_lines = window_lines
        self._stride = window_lines - overlap_lines

    def chunk(self, source_path: str, text: str) -> list[Chunk]:
        lines = text.splitlines()
        if not lines:
            return []
        chunks = []
        start = 0
        while start < len(lines):
            end = min(start + self._window_lines, len(lines))
            chunk_text = "\n".join(lines[start:end])
            chunks.append(Chunk.create(
                catalog=CATALOG, source_path=source_path, kind="window",
                start_line=start + 1, end_line=end, text=chunk_text))
            if end == len(lines):
                break
            start += self._stride
        return chunks
