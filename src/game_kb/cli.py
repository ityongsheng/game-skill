from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .core import DEFAULT_DATA_ROOT, DEFAULT_REPO_ROOT, GameKB
from .errors import GameKBError
from .routes import generate_routes


def _json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def doctor(repo_root: Path = DEFAULT_REPO_ROOT) -> dict[str, Any]:
    required = ["ffmpeg", "ffprobe", "whisper", "pdftotext", "pdftoppm", "tesseract", "soffice", "gh", "rclone"]
    tools = {name: shutil.which(name) for name in required}
    try:
        remotes = subprocess.run(["rclone", "listremotes"], check=True, text=True, capture_output=True).stdout.splitlines() if tools["rclone"] else []
    except subprocess.CalledProcessError:
        remotes = []
    try:
        ocr_languages = subprocess.run(["tesseract", "--list-langs"], check=True, text=True, capture_output=True).stdout.splitlines() if tools["tesseract"] else []
    except subprocess.CalledProcessError:
        ocr_languages = []
    gh_authenticated = False
    if tools["gh"]:
        gh_authenticated = subprocess.run(["gh", "auth", "status"], text=True, capture_output=True).returncode == 0
    try:
        git_remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=repo_root, check=True, text=True, capture_output=True).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        git_remote = ""
    checks = {
        "python": {"ok": sys.version_info >= (3, 10), "version": sys.version.split()[0]},
        "tools": {name: {"ok": bool(path), "path": path} for name, path in tools.items()},
        "rclone_remote": {"ok": bool(remotes), "remotes": remotes},
        "github_auth": {"ok": gh_authenticated},
        "git_remote": {"ok": bool(git_remote), "origin": git_remote},
        "chinese_ocr": {"ok": "chi_sim" in ocr_languages, "languages": ocr_languages},
        "sqlite_fts5": {"ok": True},
    }
    try:
        import sqlite3
        db = sqlite3.connect(":memory:")
        db.execute("CREATE VIRTUAL TABLE test_fts USING fts5(text)")
    except Exception as exc:
        checks["sqlite_fts5"] = {"ok": False, "error": str(exc)}
    ok = all((
        checks["python"]["ok"], checks["sqlite_fts5"]["ok"], checks["chinese_ocr"]["ok"],
        checks["github_auth"]["ok"], checks["git_remote"]["ok"], checks["rclone_remote"]["ok"],
        all(item["ok"] for item in checks["tools"].values()),
    ))
    return {"status": "ok" if ok else "failed", "version": __version__, "checks": checks}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="game-kb", description="Codex/Claude 可增量维护的 Game 知识库")
    root.add_argument("--data-root", type=Path, default=Path(os.environ.get("GAME_KB_ROOT", DEFAULT_DATA_ROOT)))
    root.add_argument("--repo-root", type=Path, default=Path(os.environ.get("GAME_KB_REPO", DEFAULT_REPO_ROOT)))
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("init")
    import_full = sub.add_parser("import-full")
    import_full.add_argument("path", type=Path)
    update = sub.add_parser("update")
    update.add_argument("path", type=Path)
    update.add_argument("--publish", action="store_true")
    sub.add_parser("status")
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--top-k", type=int, default=8)
    read = sub.add_parser("read")
    read.add_argument("chunk_id")
    verify = sub.add_parser("verify")
    verify.add_argument("--full", action="store_true")
    sub.add_parser("build-full")
    publish = sub.add_parser("publish")
    publish.add_argument("--dry-run", action="store_true")
    next_job = sub.add_parser("next-job")
    next_job.add_argument("--max-chars", type=int, default=12000)
    accept = sub.add_parser("accept-job")
    accept.add_argument("job_id")
    accept.add_argument("result", help="JSON 文件路径，或 - 从 stdin 读取")
    sub.add_parser("generate-routes")
    sub.add_parser("sync-installations")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor(args.repo_root)
        else:
            kb = GameKB(args.data_root, args.repo_root)
            if args.command == "init":
                result = kb.init()
            elif args.command == "import-full":
                result = kb.import_full(args.path)
            elif args.command == "update":
                result = kb.update(args.path, publish=args.publish)
            elif args.command == "status":
                result = kb.status()
            elif args.command == "search":
                result = kb.search(args.query, args.top_k)
            elif args.command == "read":
                result = kb.read(args.chunk_id)
            elif args.command == "verify":
                result = kb.verify(full=args.full)
            elif args.command == "build-full":
                result = {"status": "complete", "path": str(kb.build_full())}
            elif args.command == "publish":
                result = kb.publish(dry_run=args.dry_run)
            elif args.command == "next-job":
                result = kb.next_job(args.max_chars)
            elif args.command == "accept-job":
                raw = sys.stdin.read() if args.result == "-" else Path(args.result).read_text("utf-8")
                result = kb.accept_job(args.job_id, json.loads(raw))
            elif args.command == "generate-routes":
                result = generate_routes(args.repo_root)
            elif args.command == "sync-installations":
                result = kb.sync_installations()
            else:
                raise AssertionError(args.command)
        _json(result)
        return 0 if not isinstance(result, dict) or result.get("status") not in {"failed", "partial"} else 1
    except (GameKBError, OSError, ValueError, json.JSONDecodeError) as exc:
        _json({"status": "failed", "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
