from __future__ import annotations

import json
import mimetypes
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .database import JobStore, KnowledgeIndex
from .errors import ExtractionError, GameKBError, ValidationError
from .extractors import extract_file
from .full_io import build_full as build_full_export
from .full_io import import_full as import_full_export
from .release import build_release
from .routes import generate_routes, module_metadata
from .util import (
    append_jsonl,
    atomic_write_text,
    chunk_text,
    read_json,
    sha256_file,
    stable_slug,
    utc_now,
    write_json,
)
from .vectors import VectorIndex


DEFAULT_DATA_ROOT = Path("/Users/liyongsheng/Documents/Game知识库")
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]


class GameKB:
    def __init__(self, data_root: Path | str = DEFAULT_DATA_ROOT, repo_root: Path | str = DEFAULT_REPO_ROOT):
        self.data_root = Path(data_root).expanduser().resolve()
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.index = KnowledgeIndex(self.data_root / "index/kb.sqlite3")
        self.jobs = JobStore(self.data_root / "state/jobs.sqlite3")
        self.vectors = VectorIndex(self.data_root / "index/vectors/chunks.sqlite3")

    def init(self) -> dict[str, Any]:
        directories = (
            "inbox", "raw/sha256", "sources", "aggregates", "index/vectors",
            "state", "reports", "releases", "unparsed", "legacy",
        )
        for relative in directories:
            (self.data_root / relative).mkdir(parents=True, exist_ok=True)
        self.index.initialize()
        self.jobs.initialize()
        self.vectors.initialize()
        config = {
            "format_version": 4,
            "data_root": str(self.data_root),
            "repo_root": str(self.repo_root),
            "created_at": utc_now(),
        }
        config_path = self.data_root / "state/config.json"
        if not config_path.exists():
            write_json(config_path, config)
        return {"status": "initialized", **config}

    def _raw_target(self, digest: str, suffix: str) -> Path:
        return self.data_root / "raw/sha256" / digest[:2] / f"{digest}{suffix.lower()}"

    def _copy_immutable(self, source: Path, target: Path) -> None:
        if target.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(source, temporary)
            if sha256_file(temporary) != sha256_file(source):
                raise GameKBError(f"原件复制校验失败: {source}")
            os.replace(temporary, target)
            target.chmod(0o444)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _write_source_metadata(self, source_id: str) -> dict[str, Any]:
        record = self.index.get_source(source_id)
        if not record:
            raise GameKBError(f"来源不存在: {source_id}")
        path = self.data_root / "sources" / source_id / "source.yaml"
        metadata = {**read_json(path, {}), **record}
        metadata["aliases"] = self.index.aliases(source_id)
        write_json(path, metadata)
        return metadata

    def _make_chunks(self, source_id: str, text: str, kind: str, locator_prefix: str = "extracted") -> list[dict[str, Any]]:
        items = []
        for ordinal, value in enumerate(chunk_text(text)):
            items.append({
                "chunk_id": f"{source_id}:{ordinal:06d}", "ordinal": ordinal,
                "kind": kind, "locator": f"{locator_prefix}:{ordinal + 1}", "text": value,
            })
        return items

    def _ingest_file(self, path: Path) -> dict[str, Any]:
        digest = sha256_file(path)
        existing = self.index.source_by_hash(digest)
        if existing:
            source_id = existing["source_id"]
            self.index.add_alias(source_id, str(path.resolve()), path.name)
            if existing.get("status") != "failed":
                metadata = self._write_source_metadata(source_id)
                return {**metadata, "deduplicated": True}
        else:
            source_id = "src-" + digest[:24]
        source_dir = self.data_root / "sources" / source_id
        source_dir.mkdir(parents=True, exist_ok=True)
        raw_path = Path(existing["stored_path"]) if existing and existing.get("stored_path") else self._raw_target(digest, path.suffix)
        if not raw_path.exists():
            self._copy_immutable(path, raw_path)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        base_record = {
            "source_id": source_id, "sha256": digest, "size_bytes": path.stat().st_size,
            "media_type": media_type, "stored_path": str(raw_path), "availability": "raw_present",
            "status": "processing", "created_at": existing.get("created_at", utc_now()) if existing else utc_now(),
        }
        self.index.upsert_source(base_record)
        self.index.add_alias(source_id, str(path.resolve()), path.name)
        try:
            extraction = extract_file(path, source_dir)
            atomic_write_text(source_dir / "extracted.md", extraction.text)
            if extraction.transcript:
                atomic_write_text(
                    source_dir / "transcript.jsonl",
                    "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in extraction.transcript),
                )
            chunks = self._make_chunks(source_id, extraction.text, extraction.kind)
            self.index.replace_chunks(source_id, chunks)
            self.vectors.replace_source(source_id, chunks)
            for item in chunks:
                self.jobs.add_job("job-" + item["chunk_id"], source_id, item["chunk_id"])
            base_record["status"] = "complete"
            base_record["extraction_kind"] = extraction.kind
            base_record["warnings"] = extraction.warnings
            base_record["chunk_count"] = len(chunks)
        except Exception as exc:
            base_record["status"] = "failed"
            base_record["error"] = str(exc)
        self.index.upsert_source(base_record)
        metadata = self._write_source_metadata(source_id)
        metadata.update({key: value for key, value in base_record.items() if key not in metadata})
        if base_record["status"] == "complete":
            metadata.pop("error", None)
        write_json(source_dir / "source.yaml", {**metadata, "aliases": self.index.aliases(source_id)})
        return metadata

    def update(self, path: Path | str, publish: bool = False) -> dict[str, Any]:
        self.init()
        target = Path(path).expanduser().resolve()
        if not target.exists():
            raise ValidationError(f"输入不存在: {target}")
        files = [target] if target.is_file() else [item for item in sorted(target.rglob("*")) if item.is_file() and not item.name.startswith(".")]
        results = [self._ingest_file(item) for item in files]
        failed = [item for item in results if item.get("status") == "failed"]
        report = {
            "status": "partial" if failed else "complete",
            "input": str(target), "sources": results, "failed": len(failed), "finished_at": utc_now(),
        }
        report_path = self.data_root / "reports" / f"update-{utc_now().replace(':', '').replace('+00:00', 'Z')}.json"
        write_json(report_path, report)
        report["report"] = str(report_path)
        if publish:
            report["release"] = self.publish(dry_run=False)
            if report["release"]["status"] != "complete":
                report["status"] = "partial"
        return report

    def import_full(self, path: Path | str) -> dict[str, Any]:
        self.init()
        report = import_full_export(Path(path).expanduser().resolve(), self.data_root, self.index)
        report["vector_chunks"] = self.vectors.rebuild(self.index.iter_chunks())
        write_json(self.data_root / "reports/full-import.json", report)
        return report

    def search(self, query: str, top_k: int = 8) -> list[dict[str, Any]]:
        terms = [term.lower() for term in query.split() if term]
        module_hits = []
        for path in sorted((self.repo_root / "modules").glob("*.md")):
            text = path.read_text("utf-8")
            lowered = text.lower()
            matched = [term for term in terms if term in lowered]
            if not matched:
                continue
            position = min(lowered.find(term) for term in matched)
            start = max(0, position - 180)
            snippet = text[start : start + 800]
            try:
                module_id = module_metadata(path)["module_id"]
            except ValidationError:
                module_id = path.stem
            module_hits.append({
                "chunk_id": "module:" + path.relative_to(self.repo_root).as_posix(),
                "source_id": "module:" + module_id,
                "locator": path.relative_to(self.repo_root).as_posix(),
                "snippet": snippet,
                "score": 100 + len(matched) * 10 + (20 if len(matched) == len(terms) else 0),
                "match": "module",
            })
        module_hits.sort(key=lambda item: item["score"], reverse=True)
        lexical = self.index.search(query, top_k)
        results = module_hits + lexical
        seen = set()
        deduplicated = []
        for item in results:
            if item["chunk_id"] not in seen:
                deduplicated.append(item)
                seen.add(item["chunk_id"])
        if len(deduplicated) >= top_k:
            return deduplicated[:top_k]
        for hit in self.vectors.search(query, top_k):
            if hit["chunk_id"] in seen:
                continue
            chunk = self.index.read_chunk(hit["chunk_id"])
            if not chunk:
                continue
            deduplicated.append({
                "chunk_id": hit["chunk_id"], "source_id": hit["source_id"],
                "locator": chunk["locator"], "snippet": chunk["text"][:500],
                "score": hit["score"], "match": "vector",
            })
            seen.add(hit["chunk_id"])
            if len(deduplicated) >= top_k:
                break
        return deduplicated[:top_k]

    def read(self, chunk_id: str) -> dict[str, Any]:
        if chunk_id.startswith("module:"):
            relative = Path(chunk_id.removeprefix("module:"))
            target = (self.repo_root / relative).resolve()
            modules_root = (self.repo_root / "modules").resolve()
            if target.parent != modules_root or not target.is_file():
                raise ValidationError(f"module chunk 不存在: {chunk_id}")
            return {
                "chunk_id": chunk_id, "source_id": "module:" + target.stem,
                "ordinal": 0, "kind": "module", "locator": relative.as_posix(),
                "text": target.read_text("utf-8"),
            }
        item = self.index.read_chunk(chunk_id)
        if not item:
            raise ValidationError(f"chunk 不存在: {chunk_id}")
        return item

    def next_job(self, max_chars: int = 12000) -> dict[str, Any]:
        job = self.jobs.next_job()
        if not job:
            return {"status": "empty"}
        chunk = self.read(job["chunk_id"])
        return {
            **job,
            "status": "in_progress",
            "text": chunk["text"][:max_chars],
            "locator": chunk["locator"],
            "output_schema": {
                "$id": "game-kb-claim-v1",
                "type": "object",
                "required": ["claims"],
                "properties": {"claims": {"type": "array", "items": {"$ref": "schemas/claim.schema.json"}}},
            },
            "instructions": "只基于此来源片段提炼；观点可独立、可冲突。direct/reported/inference 仅标证据关系。quote 必须逐字出现在片段中。",
        }

    def _module_path(self, module_id: str) -> Path | None:
        for path in sorted((self.repo_root / "modules").glob("*.md")):
            try:
                if module_metadata(path)["module_id"] == module_id:
                    return path
            except ValidationError:
                continue
        return None

    def _update_modules(self, source_id: str, claims: list[dict[str, Any]]) -> list[str]:
        touched: dict[Path, list[dict[str, Any]]] = {}
        for claim in claims:
            module_id = claim.get("module_id")
            if not module_id:
                continue
            path = self._module_path(module_id)
            if path is None:
                slug = stable_slug(module_id, "new-topic")
                path = self.repo_root / "modules" / f"{slug}.md"
                title = claim.get("module_title") or module_id
                triggers = claim.get("triggers") or [title]
                frontmatter = (
                    "---\n"
                    f"module_id: {slug}\n"
                    f"title: {title}\n"
                    "triggers: [" + ", ".join(str(item) for item in triggers) + "]\n"
                    "priority: 6\n"
                    "description: 增量资料中新建的独立主题\n"
                    "---\n\n"
                    f"# {title}\n"
                )
                atomic_write_text(path, frontmatter)
            touched.setdefault(path, []).append(claim)
        updated = []
        for path, items in touched.items():
            addition = [f"\n## 增量资料 · {utc_now()}\n\n", f"来源：`{source_id}`\n\n"]
            for item in items:
                addition.append(
                    f"- **{item['claim_type']}**：{item['claim']}\n"
                    f"  - 原文：{item['quote']}\n"
                    f"  - 定位：{item.get('locator', 'source chunk')}\n"
                )
            atomic_write_text(path, path.read_text("utf-8") + "".join(addition))
            updated.append(str(path))
        if updated:
            generate_routes(self.repo_root)
        return updated

    def accept_job(self, job_id: str, result: dict[str, Any]) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            raise ValidationError(f"job 不存在: {job_id}")
        if job["status"] == "accepted":
            return {"status": "already_accepted", "job_id": job_id}
        claims = result.get("claims")
        if not isinstance(claims, list):
            raise ValidationError("输出必须包含 claims 数组")
        chunk = self.read(job["chunk_id"])
        allowed = {"direct", "reported", "inference"}
        normalized = []
        for claim in claims:
            if claim.get("claim_type") not in allowed:
                raise ValidationError("claim_type 只能是 direct/reported/inference")
            if not claim.get("claim") or not claim.get("quote"):
                raise ValidationError("每条结论必须包含 claim 和 quote")
            if claim["quote"] not in chunk["text"]:
                raise ValidationError(f"引文无法在来源片段中反向匹配: {claim['quote']}")
            normalized.append({**claim, "source_id": job["source_id"], "chunk_id": job["chunk_id"], "accepted_at": utc_now()})
        claims_path = self.data_root / "sources" / job["source_id"] / "claims.jsonl"
        append_jsonl(claims_path, normalized)
        modules = self._update_modules(job["source_id"], normalized)
        self.jobs.accept(job_id)
        return {"status": "accepted", "job_id": job_id, "claims": len(normalized), "modules": modules}

    def build_full(self) -> Path:
        self.init()
        return build_full_export(self.data_root, self.index, self.repo_root)

    def status(self) -> dict[str, Any]:
        self.init()
        return {"data_root": str(self.data_root), "repo_root": str(self.repo_root), "index": self.index.counts(), "jobs": self.jobs.counts()}

    def verify(self, full: bool = False) -> dict[str, Any]:
        self.init()
        errors: list[str] = []
        warnings: list[str] = []
        counts = self.index.counts()
        if counts["sources"] != counts["terminal_sources"]:
            errors.append("存在非终态来源")
        for source in self.index.iter_sources():
            if source["availability"] == "raw_present":
                raw = Path(source["stored_path"] or "")
                if not raw.is_file():
                    errors.append(f"原件缺失: {source['source_id']}")
                elif sha256_file(raw) != source["sha256"]:
                    errors.append(f"原件哈希不匹配: {source['source_id']}")
            claims = self.data_root / "sources" / source["source_id"] / "claims.jsonl"
            if claims.exists():
                chunk_texts = [self.index.read_chunk(row["chunk_id"])["text"] for row in map(json.loads, claims.read_text("utf-8").splitlines())]
                for raw_claim, source_text in zip(map(json.loads, claims.read_text("utf-8").splitlines()), chunk_texts):
                    if raw_claim.get("quote") not in source_text:
                        errors.append(f"引文不可回溯: {source['source_id']}:{raw_claim.get('quote')}")
        job_counts = self.jobs.counts()
        if job_counts.get("pending") or job_counts.get("in_progress"):
            warnings.append("仍有待 Agent 蒸馏的来源片段")
        if full:
            export = self.build_full()
            if not export.is_file() or export.stat().st_size == 0:
                errors.append("FULL.md 构建失败")
            import_report = self.data_root / "reports/full-import.json"
            if import_report.exists():
                baseline = json.loads(import_report.read_text("utf-8"))
                accounted = baseline.get("text_records", 0) + len(baseline.get("unparsed_blocks", []))
                if baseline.get("expected_text_records") is not None and accounted != baseline.get("expected_text_records"):
                    errors.append("FULL 基线文本记录数与声明不一致且未由 unparsed 完整解释")
                if baseline.get("expected_binary_manifest_records") is not None and baseline.get("binary_manifest_records") != baseline.get("expected_binary_manifest_records"):
                    errors.append("FULL 基线二进制清单数与声明不一致")
                if baseline.get("hash_errors") or baseline.get("byte_errors"):
                    errors.append("FULL 基线存在哈希或字节数错误")
        retrieval = []
        cases_path = self.repo_root / "tests/retrieval_cases.json"
        if full and cases_path.exists() and counts["sources"]:
            for case in json.loads(cases_path.read_text("utf-8")):
                hits = self.search(case["query"], int(case.get("top_k", 5)))
                searchable = "\n".join(str(hit.get("snippet", "")) for hit in hits)
                matched = any(expected in searchable for expected in case.get("expected_any", []))
                retrieval.append({"id": case["id"], "query": case["query"], "hits": len(hits), "matched": matched})
                if not hits or not matched:
                    errors.append(f"检索验收失败: {case['id']} ({case['query']})")
        return {"status": "ok" if not errors else "failed", "errors": errors, "warnings": warnings, "counts": counts, "jobs": job_counts, "retrieval": retrieval}

    def publish(self, dry_run: bool = False) -> dict[str, Any]:
        verification = self.verify(full=True)
        if verification["status"] != "ok":
            raise ValidationError("完整验证失败，拒绝发布: " + "; ".join(verification["errors"]))
        return build_release(self, dry_run)

    def sync_installations(self) -> dict[str, Any]:
        destinations = [Path.home() / ".codex/skills/game-skill", Path.home() / ".claude/skills/game-skill"]
        synced = []
        for destination in destinations:
            destination.parent.mkdir(parents=True, exist_ok=True)
            staging = destination.parent / f".{destination.name}.staging"
            backup_root = destination.parent / f".{destination.name}.backups"
            backup_root.mkdir(parents=True, exist_ok=True)
            stamp = utc_now().replace(":", "").replace("+00:00", "Z")
            backup = backup_root / stamp
            counter = 1
            while backup.exists():
                backup = backup_root / f"{stamp}-{counter}"
                counter += 1
            if staging.exists():
                shutil.rmtree(staging)
            shutil.copytree(
                self.repo_root, staging,
                ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".DS_Store"),
            )
            if destination.exists() or destination.is_symlink():
                os.replace(destination, backup)
            os.replace(staging, destination)
            synced.append({"path": str(destination), "backup": str(backup) if backup.exists() else None})
        return {"status": "complete", "destinations": synced}
