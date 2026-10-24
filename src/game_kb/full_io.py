from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .database import KnowledgeIndex
from .util import atomic_write_bytes, atomic_write_text, chunk_text, safe_relative_path, sha256_bytes, utc_now, write_json


RECORD_RE = re.compile(r"^###\s+(\d+)\.\s+`(.+?)`\s*$")
SHA_RE = re.compile(r"^-\s+sha256:\s+`?([0-9a-fA-F]{8,64})`?\s*$")
BYTES_RE = re.compile(r"^-\s+bytes:\s+(\d+)\s*$")
FENCE_RE = re.compile(r"^(`{3,})[^\r\n]*$", re.IGNORECASE)


def _binary_row(line: str) -> dict[str, Any] | None:
    if not line.lstrip().startswith("|") or "sha256" in line.lower() or re.match(r"^\s*\|[-: |]+\|\s*$", line):
        return None
    cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
    sha_index = next((i for i, cell in enumerate(cells) if re.fullmatch(r"[0-9a-fA-F]{64}", cell)), None)
    if sha_index is None:
        return None
    size_index = next((i for i, cell in enumerate(cells) if cell.replace(",", "").isdigit()), None)
    path_index = next((i for i, cell in enumerate(cells) if "/" in cell or Path(cell).suffix), None)
    if path_index is None:
        return None
    return {
        "path": cells[path_index],
        "size_bytes": int(cells[size_index].replace(",", "")) if size_index is not None else 0,
        "sha256": cells[sha_index].lower(),
        "status": "manifest_only",
    }


def import_full(path: Path, data_root: Path, index: KnowledgeIndex) -> dict[str, Any]:
    legacy_root = data_root / "legacy"
    unparsed_root = data_root / "unparsed"
    legacy_root.mkdir(parents=True, exist_ok=True)
    unparsed_root.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "source_full": str(path.resolve()),
        "expected_text_records": None,
        "expected_binary_manifest_records": None,
        "text_records": 0,
        "binary_manifest_records": 0,
        "hash_errors": [],
        "byte_errors": [],
        "unparsed_blocks": [],
        "started_at": utc_now(),
    }
    binary_records: list[dict[str, Any]] = []
    def preserve_unparsed(block: bytes, number: int | str, source_path: str = "unknown") -> None:
        target = unparsed_root / f"full-block-{number}.md"
        atomic_write_bytes(target, block)
        report["unparsed_blocks"].append(str(target))
        digest = sha256_bytes(block)
        source_id = f"unparsed-{number}-{digest[:16]}"
        text = block.decode("utf-8", errors="replace")
        index.upsert_source({
            "source_id": source_id, "sha256": digest, "size_bytes": len(block),
            "media_type": "text/plain", "stored_path": str(target),
            "availability": "embedded_text", "status": "failed",
        })
        index.add_alias(source_id, f"FULL:{source_path}", Path(source_path).name)
        chunks = []
        for ordinal, value in enumerate(chunk_text(text)):
            chunks.append({
                "chunk_id": f"{source_id}:{ordinal:06d}", "ordinal": ordinal,
                "kind": "unparsed", "locator": source_path, "text": value,
            })
        index.replace_chunks(source_id, chunks)

    def recover_until_next_record(handle, raw: bytearray, expected_number: int) -> None:
        while True:
            position = handle.tell()
            candidate = handle.readline()
            if not candidate:
                return
            candidate_text = candidate.decode("utf-8", errors="replace").rstrip("\r\n")
            match = RECORD_RE.match(candidate_text)
            if match and int(match.group(1)) == expected_number:
                lookahead = bytearray()
                found_sha = found_bytes = found_fence = False
                for _ in range(12):
                    peek = handle.readline()
                    if not peek:
                        break
                    lookahead.extend(peek)
                    peek_text = peek.decode("utf-8", errors="replace").rstrip("\r\n")
                    found_sha = found_sha or bool(SHA_RE.match(peek_text))
                    found_bytes = found_bytes or bool(BYTES_RE.match(peek_text))
                    found_fence = found_fence or bool(FENCE_RE.match(peek_text))
                    if found_sha and found_bytes and found_fence:
                        break
                if found_sha and found_bytes and found_fence:
                    handle.seek(position)
                    return
                raw.extend(candidate)
                raw.extend(lookahead)
                continue
            raw.extend(candidate)

    with path.open("rb") as handle:
        while True:
            line_bytes = handle.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
            match_expected_text = re.match(r"^text_files:\s*(\d+)", line)
            match_expected_binary = re.match(r"^binary_inventory_files:\s*(\d+)", line)
            if match_expected_text:
                report["expected_text_records"] = int(match_expected_text.group(1))
            if match_expected_binary:
                report["expected_binary_manifest_records"] = int(match_expected_binary.group(1))
            record_match = RECORD_RE.match(line)
            if not record_match:
                binary = _binary_row(line)
                if binary:
                    binary_records.append(binary)
                continue

            current: dict[str, Any] = {"number": int(record_match.group(1)), "path": record_match.group(2)}
            raw = bytearray(line_bytes)
            fence: str | None = None
            while True:
                metadata_bytes = handle.readline()
                if not metadata_bytes:
                    break
                raw.extend(metadata_bytes)
                metadata_line = metadata_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
                sha_match = SHA_RE.match(metadata_line)
                bytes_match = BYTES_RE.match(metadata_line)
                fence_match = FENCE_RE.match(metadata_line)
                if sha_match:
                    current["sha256"] = sha_match.group(1).lower()
                elif bytes_match:
                    current["bytes"] = int(bytes_match.group(1))
                elif fence_match:
                    fence = fence_match.group(1)
                    break
            expected_bytes = current.get("bytes")
            if fence is None or expected_bytes is None:
                recover_until_next_record(handle, raw, current["number"] + 1)
                preserve_unparsed(bytes(raw), current["number"], current["path"])
                continue

            data = handle.read(expected_bytes)
            raw.extend(data)
            closing_bytes = handle.readline()
            raw.extend(closing_bytes)
            closing = closing_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
            if closing == "":
                closing_bytes = handle.readline()
                raw.extend(closing_bytes)
                closing = closing_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
            if len(data) != expected_bytes or closing != fence:
                recover_until_next_record(handle, raw, current["number"] + 1)
                preserve_unparsed(bytes(raw), current["number"], current["path"])
                continue

            actual_sha = sha256_bytes(data)
            expected_sha = current.get("sha256")
            if expected_sha and actual_sha != expected_sha:
                report["hash_errors"].append({"path": current["path"], "expected": expected_sha, "actual": actual_sha})
            if len(data) != expected_bytes:
                report["byte_errors"].append({"path": current["path"], "expected": expected_bytes, "actual": len(data)})
            text = data.decode("utf-8", errors="replace")
            target = legacy_root / safe_relative_path(current["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(target, data)
            source_id = "legacy-" + actual_sha[:24]
            existing = index.get_source(source_id)
            if existing and existing.get("sha256") == actual_sha and existing.get("status") == "complete":
                index.add_alias(source_id, f"FULL:{current['path']}", Path(current["path"]).name)
                report["text_records"] += 1
                continue
            index.upsert_source({
                "source_id": source_id, "sha256": actual_sha, "size_bytes": len(data),
                "media_type": "text/plain", "stored_path": str(target),
                "availability": "embedded_text", "status": "complete",
            })
            index.add_alias(source_id, f"FULL:{current['path']}", Path(current["path"]).name)
            chunks = []
            for ordinal, value in enumerate(chunk_text(text)):
                chunk_id = f"{source_id}:{ordinal:06d}"
                chunks.append({"chunk_id": chunk_id, "ordinal": ordinal, "kind": "legacy", "locator": current["path"], "text": value})
            index.replace_chunks(source_id, chunks)
            report["text_records"] += 1
    manifest_path = legacy_root / "binary_manifest.jsonl"
    atomic_write_text(manifest_path, "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in binary_records))
    report["binary_manifest_records"] = len(binary_records)
    report["finished_at"] = utc_now()
    write_json(data_root / "reports/full-import.json", report)
    return report


def build_full(data_root: Path, index: KnowledgeIndex, repo_root: Path | None = None) -> Path:
    records: list[tuple[str, str]] = []
    if repo_root is not None:
        for folder in ("modules", "decision_trees", "templates"):
            base = repo_root / folder
            if not base.exists():
                continue
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    records.append((f"control/{path.relative_to(repo_root).as_posix()}", path.read_text("utf-8", errors="replace")))
    for source in index.iter_sources():
        source_id = source["source_id"]
        source_dir = data_root / "sources" / source_id
        extracted = source_dir / "extracted.md"
        if extracted.exists():
            records.append((f"sources/{source_id}/extracted.md", extracted.read_text("utf-8")))
        claims = source_dir / "claims.jsonl"
        if claims.exists():
            records.append((f"sources/{source_id}/claims.jsonl", claims.read_text("utf-8")))
    legacy_root = data_root / "legacy"
    if legacy_root.exists():
        for path in sorted(legacy_root.rglob("*")):
            if path.is_file() and path.name != "binary_manifest.jsonl":
                records.append((f"legacy/{path.relative_to(legacy_root).as_posix()}", path.read_text("utf-8", errors="replace")))
    unparsed_root = data_root / "unparsed"
    if unparsed_root.exists():
        for path in sorted(unparsed_root.rglob("*")):
            if path.is_file():
                records.append((f"unparsed/{path.relative_to(unparsed_root).as_posix()}", path.read_text("utf-8", errors="replace")))
    manifest = legacy_root / "binary_manifest.jsonl"
    manifest_count = len(manifest.read_text("utf-8").splitlines()) if manifest.exists() else 0
    lines = [
        "---\n", f"text_files: {len(records)}\n", f"binary_inventory_files: {manifest_count}\n", "format_version: 4\n", "---\n\n",
        "# Game 知识库完整导出\n\n",
        "## 二进制文件清单\n\n",
        "| path | bytes | sha256 | status |\n|---|---:|---|---|\n",
    ]
    if manifest.exists():
        for raw in manifest.read_text("utf-8").splitlines():
            item = json.loads(raw)
            lines.append(f"| `{item['path']}` | {item.get('size_bytes', 0)} | `{item['sha256']}` | {item.get('status', 'manifest_only')} |\n")
    lines.append("\n## 项目说明与最终知识层\n\n")
    for number, (name, text) in enumerate(records, 1):
        data = text.encode("utf-8")
        fence = "```"
        while fence in text:
            fence += "`"
        lines.extend([
            f"### {number}. `{name}`\n", f"- sha256: `{sha256_bytes(data)}`\n", f"- bytes: {len(data)}\n\n",
            f"{fence}markdown\n", text,
        ])
        if text and not text.endswith("\n"):
            lines.append("\n")
        lines.extend([f"{fence}\n\n"])
    target = data_root / "FULL.md"
    atomic_write_text(target, "".join(lines))
    return target
