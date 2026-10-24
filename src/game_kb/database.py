from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .util import utc_now


class KnowledgeIndex:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    stored_path TEXT,
                    availability TEXT NOT NULL DEFAULT 'raw_present',
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sources_sha ON sources(sha256);
                CREATE TABLE IF NOT EXISTS aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL REFERENCES sources(source_id),
                    original_path TEXT NOT NULL,
                    name TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    UNIQUE(source_id, original_path)
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES sources(source_id),
                    ordinal INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    locator TEXT NOT NULL,
                    text TEXT NOT NULL,
                    UNIQUE(source_id, ordinal, kind)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    chunk_id UNINDEXED, source_id UNINDEXED, locator UNINDEXED, text,
                    tokenize='unicode61'
                );
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """
            )

    def upsert_source(self, record: dict[str, Any]) -> None:
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO sources
                (source_id, sha256, size_bytes, media_type, stored_path, availability, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    media_type=excluded.media_type,
                    stored_path=excluded.stored_path,
                    availability=excluded.availability,
                    status=excluded.status,
                    updated_at=excluded.updated_at""",
                (
                    record["source_id"], record["sha256"], record["size_bytes"], record["media_type"],
                    record.get("stored_path"), record.get("availability", "raw_present"),
                    record.get("status", "complete"), record.get("created_at", now), now,
                ),
            )

    def add_alias(self, source_id: str, original_path: str, name: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO aliases(source_id, original_path, name, imported_at) VALUES (?, ?, ?, ?)",
                (source_id, original_path, name, utc_now()),
            )

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
            return dict(row) if row else None

    def source_by_hash(self, sha256: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM sources WHERE sha256=? ORDER BY created_at LIMIT 1", (sha256,)).fetchone()
            return dict(row) if row else None

    def aliases(self, source_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT original_path, name, imported_at FROM aliases WHERE source_id=? ORDER BY id", (source_id,))
            return [dict(row) for row in rows]

    def replace_chunks(self, source_id: str, chunks: list[dict[str, Any]]) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM chunks_fts WHERE source_id=?", (source_id,))
            db.execute("DELETE FROM chunks WHERE source_id=?", (source_id,))
            for item in chunks:
                db.execute(
                    "INSERT INTO chunks(chunk_id, source_id, ordinal, kind, locator, text) VALUES (?, ?, ?, ?, ?, ?)",
                    (item["chunk_id"], source_id, item["ordinal"], item["kind"], item["locator"], item["text"]),
                )
                db.execute(
                    "INSERT INTO chunks_fts(chunk_id, source_id, locator, text) VALUES (?, ?, ?, ?)",
                    (item["chunk_id"], source_id, item["locator"], item["text"]),
                )

    def search(self, query: str, top_k: int) -> list[dict[str, Any]]:
        terms = [term for term in re.split(r"\s+", query.strip()) if term]
        if not terms:
            return []
        fts_query = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        with self.connect() as db:
            try:
                rows = db.execute(
                    """SELECT chunk_id, source_id, locator,
                    snippet(chunks_fts, 3, '', '', '…', 24) AS snippet,
                    bm25(chunks_fts) AS score
                    FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?""",
                    (fts_query, top_k),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            results = [dict(row) for row in rows]
            if results:
                return results
            where = " AND ".join("text LIKE ?" for _ in terms)
            params: list[Any] = [f"%{term}%" for term in terms] + [top_k]
            rows = db.execute(
                f"SELECT chunk_id, source_id, locator, substr(text, 1, 500) AS snippet, 0.0 AS score FROM chunks WHERE {where} LIMIT ?",
                params,
            )
            return [dict(row) for row in rows]

    def read_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM chunks WHERE chunk_id=?", (chunk_id,)).fetchone()
            return dict(row) if row else None

    def counts(self) -> dict[str, int]:
        with self.connect() as db:
            return {
                "sources": db.execute("SELECT count(*) FROM sources").fetchone()[0],
                "aliases": db.execute("SELECT count(*) FROM aliases").fetchone()[0],
                "chunks": db.execute("SELECT count(*) FROM chunks").fetchone()[0],
                "terminal_sources": db.execute("SELECT count(*) FROM sources WHERE status IN ('complete','manifest_only','failed')").fetchone()[0],
            }

    def iter_sources(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM sources ORDER BY source_id").fetchall()
            return [dict(row) for row in rows]

    def iter_chunks(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM chunks ORDER BY source_id, ordinal").fetchall()
            return [dict(row) for row in rows]


class JobStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    accepted_at TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS publish_steps (
                    version TEXT NOT NULL, step TEXT NOT NULL, status TEXT NOT NULL,
                    detail TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(version, step)
                );
                """
            )

    def add_job(self, job_id: str, source_id: str, chunk_id: str) -> None:
        now = utc_now()
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO jobs(job_id, source_id, chunk_id, status, created_at, updated_at) VALUES (?, ?, ?, 'pending', ?, ?)",
                (job_id, source_id, chunk_id, now, now),
            )

    def next_job(self) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE status='in_progress' ORDER BY updated_at LIMIT 1").fetchone()
            if row is None:
                row = db.execute("SELECT * FROM jobs WHERE status='pending' ORDER BY created_at LIMIT 1").fetchone()
                if row:
                    db.execute(
                        "UPDATE jobs SET status='in_progress', attempts=attempts+1, updated_at=? WHERE job_id=?",
                        (utc_now(), row["job_id"]),
                    )
            return dict(row) if row else None

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def accept(self, job_id: str) -> None:
        now = utc_now()
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='accepted', accepted_at=?, updated_at=?, error=NULL WHERE job_id=?", (now, now, job_id))

    def counts(self) -> dict[str, int]:
        with self.connect() as db:
            return {row[0]: row[1] for row in db.execute("SELECT status, count(*) FROM jobs GROUP BY status")}
