from __future__ import annotations

import array
import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any


DIMENSIONS = 512


def embed(text: str) -> array.array:
    compact = "".join(text.lower().split())
    vector = [0.0] * DIMENSIONS
    length = len(compact)
    mask = (1 << 64) - 1
    for start, character in enumerate(compact):
        number = (ord(character) * 0x9E3779B185EBCA87) & mask
        for width in (1, 2, 3):
            if start + width > length:
                break
            if width > 1:
                number = ((number << 7) ^ (number >> 3) ^ (ord(compact[start + width - 1]) * 0xC2B2AE3D27D4EB4F)) & mask
            index = number % DIMENSIONS
            vector[index] += -1.0 if number & (1 << 63) else 1.0
    norm = math.sqrt(sum(value * value for value in vector))
    if norm:
        vector = [value / norm for value in vector]
    return array.array("f", vector)


class VectorIndex:
    """Dependency-free local Chinese character n-gram vector index."""

    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS vectors (chunk_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, vector BLOB NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS vectors_source ON vectors(source_id)")

    def replace_source(self, source_id: str, chunks: list[dict[str, Any]]) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM vectors WHERE source_id=?", (source_id,))
            for item in chunks:
                db.execute(
                    "INSERT INTO vectors(chunk_id, source_id, vector) VALUES (?, ?, ?)",
                    (item["chunk_id"], source_id, embed(item["text"]).tobytes()),
                )

    def rebuild(self, chunks: list[dict[str, Any]]) -> int:
        with self.connect() as db:
            db.execute("DELETE FROM vectors")
            db.executemany(
                "INSERT INTO vectors(chunk_id, source_id, vector) VALUES (?, ?, ?)",
                ((item["chunk_id"], item["source_id"], embed(item["text"]).tobytes()) for item in chunks),
            )
        return len(chunks)

    def search(self, query: str, top_k: int = 8) -> list[dict[str, Any]]:
        query_vector = embed(query)
        values = query_vector.tolist()
        scored: list[tuple[float, str, str]] = []
        with self.connect() as db:
            for row in db.execute("SELECT chunk_id, source_id, vector FROM vectors"):
                candidate = array.array("f")
                candidate.frombytes(row["vector"])
                score = sum(left * right for left, right in zip(values, candidate))
                scored.append((score, row["chunk_id"], row["source_id"]))
        scored.sort(reverse=True)
        return [
            {"score": score, "chunk_id": chunk_id, "source_id": source_id}
            for score, chunk_id, source_id in scored[:top_k]
            if score > 0
        ]
