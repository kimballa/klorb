# © Copyright 2026 Aaron Kimball
"""Minimal local type stub for the `sqlite-vec` package (PyPI: sqlite-vec), which ships no
`py.typed` marker and has no published `types-sqlite-vec` stub package. Covers only the `load()`
surface `klorb.search_index.store` uses to register the `vec0` virtual table module."""

from sqlite3 import Connection


def load(connection: Connection) -> None: ...
