# © Copyright 2026 Aaron Kimball
"""`MarkdownChunker`: splits a markdown file into one chunk per heading-delimited section, or one
chunk per blank-line-delimited paragraph when a section exceeds `MAX_SECTION_TOKENS`.
"""

import re

from klorb.search_index.chunk import Chunk
from klorb.search_index.chunkers.base import CATALOG, Chunker
from klorb.token_estimate import estimate_tokens

MAX_SECTION_TOKENS = 400
"""A heading-delimited section larger than this is split further into paragraphs."""

_HEADING_PATTERN = re.compile(r"^#{1,6}\s")


class MarkdownChunker(Chunker):
    def chunk(self, source_path: str, text: str, catalog: str = CATALOG) -> list[Chunk]:
        lines = text.splitlines()
        if not lines:
            return []
        chunks = []
        for start, end in _heading_sections(lines):
            section_text = "\n".join(lines[start:end])
            if estimate_tokens(section_text) <= MAX_SECTION_TOKENS:
                chunks.append(Chunk.create(
                    catalog=catalog, source_path=source_path, kind="markdown_section",
                    start_line=start + 1, end_line=end, text=section_text))
            else:
                for para_start, para_end in _paragraphs(lines, start, end):
                    chunks.append(Chunk.create(
                        catalog=catalog, source_path=source_path, kind="markdown_section",
                        start_line=para_start + 1, end_line=para_end,
                        text="\n".join(lines[para_start:para_end])))
        return chunks


def _heading_sections(lines: list[str]) -> list[tuple[int, int]]:
    """0-based `[start, end)` ranges: a leading preamble section (if any content precedes the
    first heading), then one section per heading line through the line before the next heading
    (or end of file)."""
    heading_indices = [i for i, line in enumerate(lines) if _HEADING_PATTERN.match(line)]
    boundaries = ([0] if heading_indices and heading_indices[0] > 0 else []) + heading_indices
    if not boundaries:
        return [(0, len(lines))]
    return [
        (start, boundaries[i + 1] if i + 1 < len(boundaries) else len(lines))
        for i, start in enumerate(boundaries)
    ]


def _paragraphs(lines: list[str], start: int, end: int) -> list[tuple[int, int]]:
    """0-based `[start, end)` ranges within `lines[start:end]` split on runs of one or more
    blank lines. Blank lines themselves belong to no paragraph."""
    paragraphs = []
    para_start: int | None = None
    for i in range(start, end):
        if lines[i].strip():
            if para_start is None:
                para_start = i
        elif para_start is not None:
            paragraphs.append((para_start, i))
            para_start = None
    if para_start is not None:
        paragraphs.append((para_start, end))
    return paragraphs or [(start, end)]
