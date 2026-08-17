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
from klorb.process_config import load_process_config
from klorb.search_index.catalogs import CATALOG_LABELS, WORKSPACE_CATALOG, resolve_catalogs
from klorb.search_index.chunk import Chunk
from klorb.search_index.embedding import MAX_GPU_INDEXING_THREADS, embedding_model_available
from klorb.search_index.indexer import DB_FILENAME, DEFAULT_SEARCH_LIMIT, INDEX_DIR_NAME, WorkspaceIndexer
from klorb.search_index.store import IndexStats, SearchIndexStore
from klorb.tools.util import format_match_line
from klorb.workspace import TrustManager, Workspace

_NEEDS_INIT_MESSAGE = "Embedding model not installed; run `klorb init` first."


def build_search_parser() -> argparse.ArgumentParser:
    """Build the argument parser for `klorb index search`'s own flags -- see `run_search_cli()`."""
    parser = argparse.ArgumentParser(
        prog="klorb index search",
        description="Search the workspace's local semantic search index, the same hybrid "
        "(BM25 + vector KNN) search the SemanticSearch tool uses.",
    )
    parser.add_argument("query", nargs="?", default=None, help="The search query.")
    parser.add_argument(
        "-k", "--limit", type=int, default=DEFAULT_SEARCH_LIMIT,
        help=f"Maximum number of results to return. Defaults to {DEFAULT_SEARCH_LIMIT}.",
    )
    parser.add_argument(
        "--catalog", default=WORKSPACE_CATALOG, metavar="NAME",
        help="Catalog to search. Use --list-catalogs to see available names. Defaults to 'workspace'.",
    )
    parser.add_argument(
        "--list-catalogs", action="store_true",
        help="Print the available --catalog choices with descriptions and exit.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit results as JSON, in the same shape a SemanticSearch tool result carries.",
    )
    parser.add_argument(
        "--update", dest="update", action=argparse.BooleanOptionalAction, default=False,
        help="Synchronously reindex dirty files before searching. Defaults to false: search "
        "whatever the index already holds.",
    )
    return parser


def build_scan_parser(*, default_gpu: bool = True) -> argparse.ArgumentParser:
    """Build the argument parser for `klorb index scan`'s own flags -- see `run_scan_cli()`.
    `default_gpu` sets `--gpu`'s default when neither `--gpu` nor `--no-gpu` is passed."""
    parser = argparse.ArgumentParser(
        prog="klorb index scan",
        description="Scan the workspace for dirty files not yet indexed and add them to the "
        "local search index.",
    )
    parser.add_argument(
        "-j", "--threads", type=int, default=None,
        help="Number of worker threads to use for chunking/embedding changed files. Defaults "
        f"to the machine's CPU count, capped at {MAX_GPU_INDEXING_THREADS} when embedding on GPU.",
    )
    parser.add_argument(
        "--rebuild", action="store_true", default=False,
        help="Treat every file as dirty and rebuild the index from scratch.",
    )
    parser.add_argument(
        "--gpu", dest="gpu", action=argparse.BooleanOptionalAction, default=default_gpu,
        help="Embed on GPU via CUDA when available, falling back to CPU otherwise. Defaults to "
        "the search.indexer.gpuEnabled config value; pass --gpu/--no-gpu to override.",
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


def _resolve_workspace() -> Workspace:
    return TrustManager().resolve_workspace(Path.cwd())


def _resolve_workspace_path() -> Path:
    return _resolve_workspace().path


def _list_catalogs_output(include_json: bool) -> str:
    """Human-readable listing of every catalog name and its description, or JSON if requested."""
    if include_json:
        catalogs = list(map(
            lambda pair: {"name": pair[0], "label": pair[1]}, CATALOG_LABELS.items()))
        catalogs_dict = { "catalogs": catalogs }
        return json.dumps(catalogs_dict, indent=2, ensure_ascii=False)
    lines = ["Available catalogs:"]
    lines.extend(f"  {name:<22} {CATALOG_LABELS[name]}" for name in CATALOG_LABELS)
    return "\n".join(lines)


def _chunk_to_file_entry(chunk: Chunk, score: float) -> dict[str, Any]:
    lines = [
        format_match_line(chunk.start_line + i, line, matched=True)
        for i, line in enumerate(chunk.text.splitlines())
    ]
    return {"filename": chunk.source_path, "catalog": chunk.catalog, "lines": lines, "score": score}


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
        f"{entry['filename']}  (catalog={entry['catalog']}, score {entry['score']:.3f})\n"
        + "\n".join(entry["lines"])
        for entry in entries
    ]
    return "\n\n".join(blocks)


def run_search_cli(argv: list[str]) -> int:
    """Parse `argv` (the arguments following `klorb index search`) and print the selected
    catalog's top matches for `query` to stdout. Searches whatever the index already holds
    unless `--update` is passed, in which case it claims ownership (if unowned) and runs a full
    scan synchronously before searching. With `--list-catalogs`, prints catalog names and exits.
    Returns 0 on success, 1 if the embedding model isn't installed or query is missing.
    """
    parser = build_search_parser()
    args = parser.parse_args(argv)

    if args.list_catalogs:
        print(_list_catalogs_output(args.json))
        return 0

    if args.query is None:
        parser.error("the following arguments are required: query")

    if not embedding_model_available():
        print(_NEEDS_INIT_MESSAGE, file=sys.stderr)
        return 1

    try:
        real_catalogs = resolve_catalogs(args.catalog)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    workspace_path = _resolve_workspace_path()
    indexer = WorkspaceIndexer(workspace_path)
    try:
        hits_by_id: dict[str, tuple[Chunk, float]] = {}
        for catalog in real_catalogs:
            for chunk, score in indexer.hybrid_search(
                    args.query, limit=args.limit, catalog=catalog, update=args.update):
                existing = hits_by_id.get(chunk.chunk_id)
                if existing is None or score > existing[1]:
                    hits_by_id[chunk.chunk_id] = (chunk, score)
        hits = sorted(hits_by_id.values(), key=lambda pair: pair[1], reverse=True)[:args.limit]
    finally:
        indexer.close()

    entries = _hits_to_file_entries(hits)
    if args.json:
        result = {
            "query": args.query, "top_k": args.limit, "catalog": args.catalog,
            "files": entries, "match_count": sum(len(entry["lines"]) for entry in entries),
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(_render_search_results(args.query, entries))
    return 0


def run_scan_cli(argv: list[str]) -> int:
    """Parse `argv` (the arguments following `klorb index scan`) and synchronously scan the
    workspace, indexing every dirty file across all catalogs
    (`WorkspaceIndexer.run_foreground_scan`). Returns 1 if the embedding model isn't installed or
    another process already owns the workspace's index.
    """
    cwd = Path.cwd()
    workspace = _resolve_workspace()
    process_config = load_process_config(cwd=cwd, workspace=workspace)
    parser = build_scan_parser(
        default_gpu=process_config.session.search_indexer_gpu_enabled)
    args = parser.parse_args(argv)

    if not embedding_model_available():
        print(_NEEDS_INIT_MESSAGE, file=sys.stderr)
        return 1

    if args.threads is not None:
        num_threads = args.threads
    else:
        cpu_count = os.cpu_count() or 1
        num_threads = min(cpu_count, MAX_GPU_INDEXING_THREADS) if args.gpu else cpu_count
    indexer = WorkspaceIndexer(
        workspace.path,
        index_memories=process_config.session.search_memories_index_enabled,
        index_skills=process_config.session.search_skills_index_enabled,
        claude_skills_compat=process_config.compatibility_claude_skills)
    try:
        stats = indexer.run_foreground_scan(
            rebuild=args.rebuild, num_threads=num_threads, use_gpu=args.gpu)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Scan interrupted; index left partially updated.", file=sys.stderr)
        return 130
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
        f"  Index size:      {stats.db_size_bytes / (1024.0 * 1024):.2f} MB",
    ]
    if stats.chunks_by_catalog:
        lines.append("  By catalog:")
        for catalog in sorted(stats.chunks_by_catalog):
            files = stats.files_by_catalog.get(catalog, 0)
            chunks = stats.chunks_by_catalog[catalog]
            label = CATALOG_LABELS.get(catalog, catalog)
            lines.append(f"    {catalog:<22} {files:>5} file(s), {chunks:>5} chunk(s)  ({label})")
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
            "files_by_catalog": stats.files_by_catalog,
            "chunks_by_catalog": stats.chunks_by_catalog,
            "db_size_mb": round(stats.db_size_bytes / (1024.0 * 1024.0), 3),
        }, indent=2, ensure_ascii=False))
    else:
        print(_render_stats(workspace_path, stats))
    return 0
