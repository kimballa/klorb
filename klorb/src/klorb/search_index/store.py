# © Copyright 2026 Aaron Kimball
"""`SearchIndexStore`: the SQLite-backed storage and hybrid (BM25 + vector KNN) search layer for
one workspace's chunk index -- an FTS5 virtual table for lexical search, a `sqlite-vec` `vec0`
virtual table for KNN, and a plain metadata table joining both back to full `Chunk` rows. See
docs/specs/local-search-index.md.
"""

import contextlib
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np
import pysqlite3 as sqlite3
import sqlite_vec

from klorb.lockfile import Lockfile, acquire_lockfile_with_backoff
from klorb.search_index.chunk import Chunk, ChunkKind
from klorb.search_index.embedding import EMBEDDING_DIMENSIONS
from klorb.search_index.ranking import reciprocal_rank_fusion

logger = logging.getLogger(__name__)

SCHEMA_NAME = "klorb.search_index.workspace"
SCHEMA_VERSION = "1.2.0"
"""Bumping this drops and rebuilds the whole index on next open rather than migrating it."""

_WRITE_LOCK_FILENAME = "write.lock"


@dataclass
class FileIndexRecord:
    """A previously-indexed file's whole-content hash and filesystem modification time."""

    content_hash: str
    last_modified_ts: float


@dataclass
class IndexStats:
    """Summary counts and on-disk size for `SearchIndexStore.stats()`."""

    file_count: int
    chunk_count: int
    chunk_counts_by_kind: dict[str, int]
    db_size_bytes: int


class SearchIndexStore:
    """Owns one workspace's chunk index database at `db_path`. SQLite requires a connection's calls
    to be serialized when shared across threads (the owning `WorkspaceIndexer`'s background
    scan/watcher thread writes while a tool-calling thread reads concurrently), so every method
    holds `self._thread_lock` for its own `self._conn` calls.

    Every write method additionally acquires the short-lived `write.lock` (via `klorb.lockfile`)
    around its own transaction as defense in depth around the owner-lock handoff race described in
    docs/specs/local-search-index.md. A caller making many writes in a row can instead hold the
    lock itself via `acquire_write_lock()` and pass it into each write call, paying the file-lock
    cost once rather than per call.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._write_lock_path = db_path.parent / _WRITE_LOCK_FILENAME
        self._thread_lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._ensure_schema()

    def close(self) -> None:
        with self._thread_lock:
            self._conn.close()

    def _ensure_schema(self) -> None:
        with self._thread_lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = 'schema'").fetchone()
            current = f"{SCHEMA_NAME}:{SCHEMA_VERSION}"
            if row is not None and row[0] == current:
                return
            if row is not None:
                logger.warning(
                    "Search index schema at %s is %r, expected %r; rebuilding.",
                    self._db_path, row[0], current)
            self._conn.execute("DROP TABLE IF EXISTS chunks")
            self._conn.execute("DROP TABLE IF EXISTS chunks_fts")
            self._conn.execute("DROP TABLE IF EXISTS chunks_vec")
            self._conn.execute("DROP TABLE IF EXISTS files")
            self._conn.execute(
                "CREATE TABLE chunks ("
                " chunk_id TEXT PRIMARY KEY, catalog TEXT NOT NULL, source_path TEXT NOT NULL,"
                " kind TEXT NOT NULL, start_line INTEGER NOT NULL, end_line INTEGER NOT NULL,"
                " text TEXT NOT NULL, token_count INTEGER NOT NULL, content_hash TEXT NOT NULL)")
            self._conn.execute(
                "CREATE INDEX idx_chunks_source_path ON chunks(source_path)")
            self._conn.execute(
                "CREATE TABLE files (source_path TEXT PRIMARY KEY, content_hash TEXT NOT NULL, "
                "last_modified_ts REAL NOT NULL)")
            self._conn.execute(
                "CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, catalog UNINDEXED, body)")
            self._conn.execute(
                f"CREATE VIRTUAL TABLE chunks_vec USING vec0("
                f" chunk_id TEXT PRIMARY KEY, catalog TEXT PARTITION KEY,"
                f" embedding FLOAT[{EMBEDDING_DIMENSIONS}])")
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES ('schema', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (current,))
            self._conn.commit()
            logger.debug("Search index schema (re)created at %s.", self._db_path)

    def acquire_write_lock(self) -> "WriteLock":
        """Acquire this store's `write.lock` for the duration of a `with` block, so a caller
        making many write calls in a row can pass the returned lock into each one instead of
        re-acquiring the file lock per call."""
        return WriteLock(self._write_lock_path)

    def _write_scope(
        self, write_lock: "WriteLock | None",
    ) -> "contextlib.AbstractContextManager[WriteLock | None]":
        if write_lock is not None:
            return contextlib.nullcontext()
        return WriteLock(self._write_lock_path)

    def upsert_chunks(
        self, chunks: list[Chunk], embeddings: list[np.ndarray],
        write_lock: "WriteLock | None" = None,
    ) -> None:
        """Insert or replace `chunks` (and their parallel `embeddings`) in all three tables,
        keyed by `chunk_id`. `chunks` and `embeddings` must be the same length and order."""
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"chunks ({len(chunks)}) and embeddings ({len(embeddings)}) must be the same length")
        if not chunks:
            return
        with self._write_scope(write_lock), self._thread_lock:
            for chunk, embedding in zip(chunks, embeddings):
                self._conn.execute(
                    "INSERT INTO chunks(chunk_id, catalog, source_path, kind, start_line, "
                    "end_line, text, token_count, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(chunk_id) DO UPDATE SET catalog=excluded.catalog, "
                    "source_path=excluded.source_path, kind=excluded.kind, "
                    "start_line=excluded.start_line, end_line=excluded.end_line, "
                    "text=excluded.text, token_count=excluded.token_count, "
                    "content_hash=excluded.content_hash",
                    (chunk.chunk_id, chunk.catalog, chunk.source_path, chunk.kind,
                     chunk.start_line, chunk.end_line, chunk.text, chunk.token_count,
                     chunk.content_hash))
                self._conn.execute(
                    "DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk.chunk_id,))
                self._conn.execute(
                    "INSERT INTO chunks_fts(chunk_id, catalog, body) VALUES (?, ?, ?)",
                    (chunk.chunk_id, chunk.catalog, chunk.text))
                self._conn.execute(
                    "DELETE FROM chunks_vec WHERE chunk_id = ?", (chunk.chunk_id,))
                self._conn.execute(
                    "INSERT INTO chunks_vec(chunk_id, catalog, embedding) VALUES (?, ?, ?)",
                    (chunk.chunk_id, chunk.catalog, np.asarray(embedding, dtype=np.float32).tobytes()))
            self._conn.commit()
        logger.debug("Upserted %d chunk(s) into %s.", len(chunks), self._db_path)

    def delete_for_path(self, source_path: str, write_lock: "WriteLock | None" = None) -> None:
        """Delete every chunk indexed for `source_path` (a file removed, or about to be
        rechunked from scratch), and its `files` bookkeeping row."""
        with self._write_scope(write_lock), self._thread_lock:
            chunk_ids = [
                row[0] for row in self._conn.execute(
                    "SELECT chunk_id FROM chunks WHERE source_path = ?", (source_path,))]
            for chunk_id in chunk_ids:
                self._conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_id,))
                self._conn.execute("DELETE FROM chunks_vec WHERE chunk_id = ?", (chunk_id,))
            self._conn.execute("DELETE FROM chunks WHERE source_path = ?", (source_path,))
            self._conn.execute("DELETE FROM files WHERE source_path = ?", (source_path,))
            self._conn.commit()
        if chunk_ids:
            logger.debug("Deleted %d chunk(s) for %s from %s.",
                         len(chunk_ids), source_path, self._db_path)

    def set_file_hash(
        self, source_path: str, content_hash: str, last_modified_ts: float,
        write_lock: "WriteLock | None" = None,
    ) -> None:
        """Record `source_path`'s whole-file `content_hash` and filesystem `last_modified_ts` --
        distinct from any individual chunk's `Chunk.content_hash` -- so `file_records()` can
        answer "did this file change since it was last indexed" unambiguously, without needing to
        pick among a file's several chunks."""
        with self._write_scope(write_lock), self._thread_lock:
            self._conn.execute(
                "INSERT INTO files(source_path, content_hash, last_modified_ts) VALUES (?, ?, ?) "
                "ON CONFLICT(source_path) DO UPDATE SET content_hash = excluded.content_hash, "
                "last_modified_ts = excluded.last_modified_ts",
                (source_path, content_hash, last_modified_ts))
            self._conn.commit()

    def file_records(self) -> dict[str, FileIndexRecord]:
        """Every currently-indexed `source_path` mapped to its `FileIndexRecord` -- used by the
        indexer to skip re-reading and re-hashing a file whose mtime hasn't changed, and to detect
        files that changed since they were last chunked."""
        with self._thread_lock:
            rows = self._conn.execute(
                "SELECT source_path, content_hash, last_modified_ts FROM files").fetchall()
        return {
            row[0]: FileIndexRecord(content_hash=row[1], last_modified_ts=row[2]) for row in rows
        }

    def clear(self, write_lock: "WriteLock | None" = None) -> None:
        """Delete every row from `chunks`/`chunks_fts`/`chunks_vec`/`files`, leaving the schema
        itself intact -- used by `klorb index scan --rebuild` to force a full reindex from
        scratch."""
        with self._write_scope(write_lock), self._thread_lock:
            self._conn.execute("DELETE FROM chunks")
            self._conn.execute("DELETE FROM chunks_fts")
            self._conn.execute("DELETE FROM chunks_vec")
            self._conn.execute("DELETE FROM files")
            self._conn.commit()
        logger.debug("Cleared all rows from %s.", self._db_path)

    def stats(self) -> IndexStats:
        """Summary counts over this store's current contents and `db_path`'s on-disk size,
        including its WAL/SHM sidecar files since a recent write may still be sitting in the
        WAL rather than the main database file."""
        with self._thread_lock:
            file_count = self._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            chunk_count = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            kind_rows = self._conn.execute(
                "SELECT kind, COUNT(*) FROM chunks GROUP BY kind").fetchall()
        db_size_bytes = sum(
            path.stat().st_size for path in self._db_path.parent.glob(f"{self._db_path.name}*")
            if path.is_file())
        return IndexStats(
            file_count=file_count, chunk_count=chunk_count,
            chunk_counts_by_kind=dict(kind_rows), db_size_bytes=db_size_bytes)

    def search_lexical(self, query: str, limit: int, catalog: str) -> list[str]:
        """Return up to `limit` `chunk_id`s matching `query` within `catalog` via FTS5, ranked by
        BM25 (most relevant first). `query` is escaped as a set of quoted FTS5 tokens, so
        punctuation in a natural-language query can never be parsed as FTS5 query syntax."""
        fts_query = " ".join(f'"{term}"' for term in query.split() if term)
        if not fts_query:
            return []
        with self._thread_lock:
            rows = self._conn.execute(
                "SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ? AND catalog = ? "
                "ORDER BY bm25(chunks_fts) LIMIT ?",
                (fts_query, catalog, limit)).fetchall()
        return [row[0] for row in rows]

    def search_vector(self, query_embedding: np.ndarray, limit: int, catalog: str) -> list[str]:
        """Return up to `limit` `chunk_id`s within `catalog` nearest to `query_embedding` via
        `vec0` KNN (closest first). `catalog` is a `vec0` partition key, so this is a true
        within-catalog nearest-neighbor search, not a global top-`limit` search filtered
        afterward."""
        with self._thread_lock:
            rows = self._conn.execute(
                "SELECT chunk_id FROM chunks_vec WHERE embedding MATCH ? AND catalog = ? "
                "AND k = ? ORDER BY distance",
                (np.asarray(query_embedding, dtype=np.float32).tobytes(), catalog, limit)).fetchall()
        return [row[0] for row in rows]

    def hybrid_search(
        self, query_text: str, query_embedding: np.ndarray, limit: int, catalog: str,
    ) -> list[tuple[Chunk, float]]:
        """Fuse `search_lexical`/`search_vector` (each capped to `limit`, both scoped to
        `catalog`) via `reciprocal_rank_fusion`, then hydrate the top `limit` fused results into
        full `Chunk` rows paired with their fused score."""
        lexical_ids = self.search_lexical(query_text, limit, catalog)
        vector_ids = self.search_vector(query_embedding, limit, catalog)
        fused = reciprocal_rank_fusion([lexical_ids, vector_ids])[:limit]
        chunks_by_id = self._load_chunks([chunk_id for chunk_id, _ in fused])
        return [
            (chunks_by_id[chunk_id], score) for chunk_id, score in fused if chunk_id in chunks_by_id
        ]

    def _load_chunks(self, chunk_ids: list[str]) -> dict[str, Chunk]:
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" for _ in chunk_ids)
        with self._thread_lock:
            rows = self._conn.execute(
                f"SELECT chunk_id, catalog, source_path, kind, start_line, end_line, text, "
                f"token_count, content_hash FROM chunks WHERE chunk_id IN ({placeholders})",
                chunk_ids).fetchall()
        result = {}
        for row in rows:
            (chunk_id, catalog, source_path, kind, start_line, end_line, text,
             token_count, content_hash) = row
            result[chunk_id] = Chunk(
                chunk_id=chunk_id, catalog=catalog, source_path=source_path,
                kind=cast(ChunkKind, kind), start_line=start_line, end_line=end_line, text=text,
                token_count=token_count, content_hash=content_hash)
        return result


class WriteLock:
    """Context manager acquiring `path` via `acquire_lockfile_with_backoff` on enter, releasing
    it on exit. Raises `RuntimeError` if the lock couldn't be acquired -- a write that can't get
    exclusive access must fail loudly, not proceed and risk corrupting the index."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock: Lockfile | None = None

    def __enter__(self) -> "WriteLock":
        lock = acquire_lockfile_with_backoff(self._path)
        if lock is None:
            raise RuntimeError(f"Could not acquire search index write lock at {self._path}.")
        self._lock = lock
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._lock is not None:
            self._lock.release()
            self._lock = None
