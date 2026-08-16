# © Copyright 2026 Aaron Kimball
"""`ChunkerRouter`: dispatches a file to the structural/markdown chunker matching its extension,
plus the windowed chunker unconditionally, per docs/specs/local-search-index.md.
"""

from klorb.search_index.chunk import Chunk
from klorb.search_index.chunkers.base import Chunker
from klorb.search_index.chunkers.code_python import PythonChunker
from klorb.search_index.chunkers.code_typescript import TsxChunker, TypeScriptChunker
from klorb.search_index.chunkers.markdown import MarkdownChunker
from klorb.search_index.chunkers.windowed import WindowedChunker

_EXTENSION_CHUNKERS: dict[str, Chunker] = {
    ".py": PythonChunker(),
    ".ts": TypeScriptChunker(),
    ".tsx": TsxChunker(),
    ".md": MarkdownChunker(),
    ".markdown": MarkdownChunker(),
    ".txt": MarkdownChunker(),
}


class ChunkerRouter:
    """Chunks a file with its extension-matched chunker (if any) plus the windowed chunker,
    always. A parse failure in the extension-matched chunker (an empty result, or an exception)
    still leaves the windowed chunker's coverage intact."""

    def __init__(self) -> None:
        self._extension_chunkers = _EXTENSION_CHUNKERS
        self._windowed_chunker = WindowedChunker()

    def chunk_file(self, source_path: str, text: str) -> list[Chunk]:
        chunks: list[Chunk] = []
        extension = _extension_of(source_path)
        matched = self._extension_chunkers.get(extension)
        if matched is not None:
            chunks.extend(matched.chunk(source_path, text))
        chunks.extend(self._windowed_chunker.chunk(source_path, text))
        return chunks


def _extension_of(source_path: str) -> str:
    dot_index = source_path.rfind(".")
    return source_path[dot_index:].lower() if dot_index != -1 else ""


_chunker_router: ChunkerRouter | None = None


def get_chunker_router() -> ChunkerRouter:
    """Return the shared `ChunkerRouter`, constructing it (and caching the result) on first use."""
    global _chunker_router
    if _chunker_router is None:
        _chunker_router = ChunkerRouter()
    return _chunker_router
