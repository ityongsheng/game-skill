from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_text(path: Path, value: str) -> None:
    atomic_write_bytes(path, value.encode("utf-8"))


def write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    addition = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in values)
    atomic_write_text(path, current + addition)


def safe_relative_path(value: str) -> Path:
    value = value.replace("\\", "/").lstrip("/")
    parts = [part for part in value.split("/") if part not in ("", ".", "..")]
    return Path(*parts) if parts else Path("unnamed")


def stable_slug(value: str, fallback: str = "source") -> str:
    original = value.strip()
    value = original.lower().replace("_", "-")
    value = re.sub(r"[^a-z0-9-]+", "-", value).strip("-")
    return value[:80] or f"{fallback}-{hashlib.sha256(original.encode('utf-8')).hexdigest()[:10]}"


def chunk_text(text: str, max_chars: int = 4000, overlap: int = 200) -> list[str]:
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(length, start + max_chars)
        if end < length:
            boundary = max(text.rfind("\n\n", start, end), text.rfind("。", start, end))
            if boundary > start + max_chars // 2:
                end = boundary + (1 if text[boundary] == "。" else 2)
        chunks.append(text[start:end])
        if end >= length:
            break
        start = max(start + 1, end - overlap)
    return chunks
