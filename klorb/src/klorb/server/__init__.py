# © Copyright 2026 Aaron Kimball
"""The `klorb.server` package: a persistent, non-interactive klorb process that speaks
newline-delimited JSON (JSONL) over stdin/stdout, for driving klorb from another program
rather than a terminal. See docs/specs/klorb-server.md.

This top-level module re-exports `JsonlServer` (`klorb.server.jsonl_server`) for callers
outside this package (`klorb.cli`) to import directly.
"""

from klorb.server.jsonl_server import JsonlServer

__all__ = [
    "JsonlServer",
]
