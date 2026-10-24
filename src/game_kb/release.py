from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from .errors import PublishError
from .full_io import build_full
from .routes import generate_routes
from .util import atomic_write_text, read_json, sha256_file, utc_now, write_json


def bump_patch(version: str) -> str:
    major, minor, patch = (int(item) for item in version.strip().split("."))
    return f"{major}.{minor}.{patch + 1}"


def _run(command: list[str], cwd: Path) -> str:
    try:
        result = subprocess.run(command, cwd=cwd, check=True, text=True, capture_output=True)
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or getattr(exc, "stdout", "") or str(exc)
        raise PublishError(f"发布命令失败: {' '.join(command)}\n{detail.strip()}") from exc


def _zip_skill(repo_root: Path, target: Path) -> None:
    ignored = {".git", "__pycache__", ".pytest_cache", ".DS_Store"}
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(repo_root.rglob("*")):
            relative = path.relative_to(repo_root)
            if not path.is_file() or any(part in ignored for part in relative.parts):
                continue
            if relative.parts and relative.parts[0] == "tests":
                continue
            archive.write(path, Path("game-skill") / relative)


def _asset(path: Path) -> dict[str, Any]:
    return {"name": path.name, "path": str(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}


def _ensure_github_release(repo_root: Path, version: str, assets: list[Path]) -> str:
    tag = f"v{version}"
    exists = subprocess.run(["gh", "release", "view", tag], cwd=repo_root, text=True, capture_output=True).returncode == 0
    if exists:
        return _run(["gh", "release", "upload", tag, *(str(path) for path in assets), "--clobber"], repo_root)
    return _run([
        "gh", "release", "create", tag, *(str(path) for path in assets),
        "--title", f"game-skill v{version}", "--generate-notes",
    ], repo_root)


def build_release(kb, dry_run: bool) -> dict[str, Any]:
    repo_root = kb.repo_root
    current = (repo_root / "VERSION").read_text("utf-8").strip()
    resumable_state = kb.data_root / "releases" / f"v{current}" / "publish-state.json"
    resumable = not dry_run and resumable_state.exists() and read_json(resumable_state, {}).get("status") == "partial"
    version = current if resumable else bump_patch(current)
    release_dir = kb.data_root / "releases" / f"v{version}"
    release_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = release_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    full = build_full(kb.data_root, kb.index, repo_root)
    if not dry_run and not resumable:
        atomic_write_text(repo_root / "VERSION", version + "\n")
    generate_routes(repo_root)
    package = assets_dir / f"game-skill-v{version}.zip"
    full_gz = assets_dir / f"game-skill-v{version}-FULL.md.gz"
    _zip_skill(repo_root, package)
    with full.open("rb") as source, gzip.open(full_gz, "wb", compresslevel=9) as target:
        shutil.copyfileobj(source, target)
    manifest_path = assets_dir / "release_manifest.json"
    local_assets = [_asset(package), _asset(full_gz)]
    manifest = {
        "name": "game-skill", "version": version, "created_at": utc_now(),
        "dry_run": dry_run,
        "assets": [{key: value for key, value in item.items() if key != "path"} for item in local_assets],
    }
    write_json(manifest_path, manifest)
    local_manifest = _asset(manifest_path)
    repository_manifest = {**manifest, "assets": manifest["assets"] + [{key: value for key, value in local_manifest.items() if key != "path"}]}
    write_json(repo_root / "index/release_manifest.json", repository_manifest)
    result = {"status": "dry_run" if dry_run else "prepared", "version": version, "assets": local_assets + [local_manifest], "steps": []}
    if dry_run:
        return result

    state_path = release_dir / "publish-state.json"
    state = read_json(state_path, {"version": version, "status": "running", "steps": {}, "updated_at": utc_now()})
    state["status"] = "running"

    def step(name: str, action) -> None:
        if state["steps"].get(name, {}).get("status") == "complete":
            return
        try:
            detail = action()
            state["steps"][name] = {"status": "complete", "detail": detail, "at": utc_now()}
        except Exception as exc:
            state["steps"][name] = {"status": "failed", "detail": str(exc), "at": utc_now()}
            state["status"] = "partial"
            write_json(state_path, state)
            raise
        write_json(state_path, state)

    try:
        step("verify", lambda: kb.verify(full=True))
        step("git_commit", lambda: (_run(["git", "add", "SKILL.md", "KB_REFERENCE.md", "VERSION", "agents", "modules", "decision_trees", "templates", "index", "schemas", "src", "scripts", "config", "tests", "skill.yaml", "README.md"], repo_root), _run(["git", "commit", "-m", f"release: game-skill v{version}"], repo_root))[1])
        step("git_push", lambda: _run(["git", "push", "origin", "main"], repo_root))
        step("github_release", lambda: _ensure_github_release(repo_root, version, [package, full_gz, manifest_path]))
        remote = os.environ.get("GAME_KB_RCLONE_REMOTE", "gdrive:").rstrip(":")
        drive_dir = f"{remote}:game-skill/releases/v{version}/"
        step("drive_upload", lambda: _run(["rclone", "copy", str(assets_dir), drive_dir, "--checksum"], repo_root))
        step("drive_verify", lambda: _run(["rclone", "check", str(assets_dir), drive_dir, "--one-way", "--checksum"], repo_root))
        latest = kb.data_root / "releases/latest"
        latest.mkdir(exist_ok=True)
        shutil.copy2(manifest_path, latest / "release_manifest.json")
        step("drive_latest", lambda: _run(["rclone", "copyto", str(latest / "release_manifest.json"), f"{remote}:game-skill/latest/release_manifest.json"], repo_root))
        step("sync_installations", lambda: kb.sync_installations())
        state["status"] = "complete"
    except Exception:
        result["status"] = "partial"
        result["steps"] = state["steps"]
        return result
    state["updated_at"] = utc_now()
    write_json(state_path, state)
    result["status"] = "complete"
    result["steps"] = state["steps"]
    return result
