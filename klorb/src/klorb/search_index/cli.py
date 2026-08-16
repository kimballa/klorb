# © Copyright 2026 Aaron Kimball
"""Sub-main entry points for `klorb index`'s actions (`search`/`scan`/`stats`), dispatched to by
`klorb.cli.index.run_index_cli`. See docs/specs/local-search-index.md.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from klorb.permissions.directory_access import workspace_klorb_dir
from klorb.search_index.chunk import Chunk
from klorb.search_index.embedding import embedding_model_available
from klorb.search_index.indexer import DB_FILENAME, DEFAULT_SEARCH_LIMIT, INDEX_DIR_NAME, WorkspaceIndexer
from klorb.search_index.store import IndexStats, SearchIndexStore
from klorb.tools.util import format_match_line
from klorb.workspace import TrustManager

_NEEDS_INIT_MESSAGE = "Embedding model not installed; run `klorb init` first."


def build_search_parser() -> argparse.ArgumentParser:
    """Build the argument parser for `klorb index search`'s own flags -- see `run_search_cli()`."""
    parser = argparse.ArgumentParser(
        prog="klorb index search",
        description="Search the workspace's local semantic search index, the same hybrid "
        "(BM25 + vector KNN) search the SemanticSearch tool uses.",
    )
    parser.add_argument("query", help="The search query.")
    parser.add_argument(
        "-k", "--limit", type=int, default=DEFAULT_SEARCH_LIMIT,
        help=f"Maximum number of results to return. Defaults to {DEFAULT_SEARCH_LIMIT}.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit results as JSON, in the same shape a SemanticSearch tool result carries.",
    )
    return parser


def build_scan_parser() -> argparse.ArgumentParser:
    """Build the argument parser for `klorb index scan`'s own flags -- see `run_scan_cli()`."""
    parser = argparse.ArgumentParser(
        prog="klorb index scan",
        description="Scan the workspace for dirty files not yet indexed and add them to the "
        "local search index.",
    )
    parser.add_argument(
        "-j", "--threads", type=int, default=None,
        help="Number of worker threads to use for chunking/embedding changed files. Defaults "
        "to the machine's CPU count.",
    )
    parser.add_argument(
        "--rebuild", action="store_true", default=False,
        help="Treat every file as dirty and rebuild the index from scratch.",
    )
    parser.add_argument(
        "--gpu", action="store_true", default=False,
        help="Embed on GPU via CUDA instead of CPU. Requires onnxruntime-gpu (and matching "
        "installed (not a default klorb dependency); fails with an explanatory error otherwise.",
    )
    return parser


def build_stats_parser() -> argparse.ArgumentParser:
    """Build the argument parser for `klorb index stats`'s own flags -- see `run_stats_cli()`."""
    parser = argparse.ArgumentParser(
        prog="klorb index stats",
        description="Show summary statistics for the workspace's local search index.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the stats as JSON.")
    return parser


def _resolve_workspace_path() -> Path:
    return TrustManager().resolve_workspace(Path.cwd()).path


def _chunk_to_file_entry(chunk: Chunk, score: float) -> dict[str, Any]:
    lines = [
        format_match_line(chunk.start_line + i, line, matched=True)
        for i, line in enumerate(chunk.text.splitlines())
    ]
    return {"filename": chunk.source_path, "lines": lines, "score": score}


def _hits_to_file_entries(hits: list[tuple[Chunk, float]]) -> list[dict[str, Any]]:
    """Group `hits` (chunk/score pairs, already ranked) by file in the same shape the
    SemanticSearch tool's result carries: each entry's `lines` are dense-format (`*line|text`,
    every line of a matched chunk marked), and `score` is the file's best-scoring chunk."""
    entries_by_path: dict[str, dict[str, Any]] = {}
    for chunk, score in hits:
        entry = entries_by_path.get(chunk.source_path)
        if entry is None:
            entries_by_path[chunk.source_path] = _chunk_to_file_entry(chunk, score)
        else:
            entry["lines"].extend(_chunk_to_file_entry(chunk, score)["lines"])
            entry["score"] = max(entry["score"], score)
    return sorted(entries_by_path.values(), key=lambda entry: entry["score"], reverse=True)


def _render_search_results(query: str, entries: list[dict[str, Any]]) -> str:
    if not entries:
        return f"No semantic matches for {query!r}."
    blocks = [
        f"{entry['filename']}  (score {entry['score']:.3f})\n" + "\n".join(entry["lines"])
        for entry in entries
    ]
    return "\n\n".join(blocks)


def run_search_cli(argv: list[str]) -> int:
    """Parse `argv` (the arguments following `klorb index search`) and print the `workspace`
    catalog's top matches for `query` to stdout, in the same result shape the `SemanticSearch`
    tool returns. If no process currently owns the workspace's index, this call claims ownership
    and runs a full scan synchronously before searching. Returns 0 on success, 1 if the embedding
    model isn't installed.
    """
    parser = build_search_parser()
    args = parser.parse_args(argv)

    if not embedding_model_available():
        print(_NEEDS_INIT_MESSAGE, file=sys.stderr)
        return 1

    workspace_path = _resolve_workspace_path()
    indexer = WorkspaceIndexer(workspace_path)
    try:
        hits = indexer.hybrid_search(args.query, limit=args.limit)
    finally:
        indexer.close()

    entries = _hits_to_file_entries(hits)
    if args.json:
        result = {
            "query": args.query, "top_k": args.limit,
            "files": entries, "match_count": sum(len(entry["lines"]) for entry in entries),
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(_render_search_results(args.query, entries))
    return 0


def run_scan_cli(argv: list[str]) -> int:
    """Parse `argv` (the arguments following `klorb index scan`) and synchronously scan both the
    `workspace` and `memories-workspace` catalogs, indexing every dirty file
    (`WorkspaceIndexer.run_foreground_scan`). `--threads`/`-j` defaults to `os.cpu_count()`;
    `--rebuild` clears the index first so every file is treated as dirty; `--gpu` embeds on CUDA
    instead of CPU. A Ctrl-C mid-scan returns 0, leaving the index however far the scan got.
    Returns 1 if the embedding model isn't installed, another process already owns the
    workspace's index, or `--gpu` was given but CUDA isn't available.
    """
    parser = build_scan_parser()
    args = parser.parse_args(argv)

    if not embedding_model_available():
        print(_NEEDS_INIT_MESSAGE, file=sys.stderr)
        return 1

    num_threads = args.threads if args.threads is not None else (os.cpu_count() or 1)
    workspace_path = _resolve_workspace_path()
    indexer = WorkspaceIndexer(workspace_path)
    try:
        stats = indexer.run_foreground_scan(
            rebuild=args.rebuild, num_threads=num_threads, use_gpu=args.gpu)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Scan interrupted; index left partially updated.", file=sys.stderr)
        return 0
    finally:
        indexer.close()

    print(
        f"Scanned {stats.files_scanned} file(s): indexed {stats.files_indexed}, "
        f"removed {stats.files_removed}, wrote {stats.chunks_indexed} chunk(s) "
        f"({stats.elapsed_seconds:.1f}s).")
    return 0


def _render_stats(workspace_path: Path, stats: IndexStats) -> str:
    lines = [
        f"Search index for {workspace_path}",
        f"  Files indexed:   {stats.file_count}",
        f"  Chunks indexed:  {stats.chunk_count}",
        f"  Index size:      {stats.db_size_bytes / (1024 * 1024):.2f} MB",
    ]
    if stats.chunk_counts_by_kind:
        lines.append("  Chunks by kind:")
        lines.extend(
            f"    {kind}: {stats.chunk_counts_by_kind[kind]}"
            for kind in sorted(stats.chunk_counts_by_kind))
    return "\n".join(lines)


def run_stats_cli(argv: list[str]) -> int:
    """Parse `argv` (the arguments following `klorb index stats`) and print summary statistics
    for the workspace's local search index (file/chunk counts, on-disk size) to stdout, either
    as a human-readable report or (`--json`). Returns 0 on success, 1 if the workspace has no
    index yet.
    """
    parser = build_stats_parser()
    args = parser.parse_args(argv)

    workspace_path = _resolve_workspace_path()
    db_path = workspace_klorb_dir(workspace_path) / INDEX_DIR_NAME / DB_FILENAME
    if not db_path.exists():
        message = f"No search index found at {db_path}. Run `klorb index scan` first."
        if args.json:
            print(json.dumps({"error": message}))
        else:
            print(message)
        return 1

    store = SearchIndexStore(db_path)
    try:
        stats = store.stats()
    finally:
        store.close()

    if args.json:
        print(json.dumps({
            "workspace": str(workspace_path),
            "file_count": stats.file_count,
            "chunk_count": stats.chunk_count,
            "chunk_counts_by_kind": stats.chunk_counts_by_kind,
            "db_size_mb": stats.db_size_bytes / (1024 * 1024),
        }, indent=2, ensure_ascii=False))
    else:
        print(_render_stats(workspace_path, stats))
    return 0
