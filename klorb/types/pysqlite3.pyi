# © Copyright 2026 Aaron Kimball
"""Minimal local type stub for the `pysqlite3-binary` package (PyPI: pysqlite3-binary), which
ships no `py.typed` marker and has no published `types-pysqlite3` stub package. `pysqlite3` is a
drop-in replacement for the standard library's `sqlite3` module (same `Connection`/`Cursor`
interface, a self-contained modern SQLite build), so this stub re-exports stdlib `sqlite3`'s own
typed `Connection` rather than redeclaring its interface."""

from sqlite3 import Connection as Connection


def connect(database: str, *, check_same_thread: bool = ...) -> Connection: ...


sqlite_version: str
