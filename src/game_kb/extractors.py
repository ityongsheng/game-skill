from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from .errors import ExtractionError
from .util import atomic_write_text


@dataclass
class Extraction:
    text: str
    kind: str
    transcript: list[dict] = field(default_factory=list)
    frames: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class _TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"p", "div", "br", "h1", "h2", "h3", "h4", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"p", "div", "h1", "h2", "h3", "h4", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        value = "".join(self.parts)
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip() + "\n"


def _decode(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "gb18030", "big5"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, cwd=cwd, check=True, text=True, capture_output=True)
    except FileNotFoundError as exc:
        raise ExtractionError(f"缺少提取工具: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise ExtractionError(f"提取命令失败 ({command[0]}): {detail}") from exc


def _docx(path: Path) -> Extraction:
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    tables: list[str] = []
    comments: list[str] = []
    with zipfile.ZipFile(path) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
        for paragraph in document.findall(".//w:body/w:p", ns):
            text = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns)).strip()
            if text:
                paragraphs.append(text)
        for row in document.findall(".//w:tbl/w:tr", ns):
            cells = []
            for cell in row.findall("./w:tc", ns):
                value = "".join(node.text or "" for node in cell.findall(".//w:t", ns)).strip()
                cells.append(value)
            if any(cells):
                tables.append(" | ".join(cells))
        if "word/comments.xml" in archive.namelist():
            tree = ET.fromstring(archive.read("word/comments.xml"))
            for comment in tree.findall(".//w:comment", ns):
                text = "".join(node.text or "" for node in comment.findall(".//w:t", ns)).strip()
                if text:
                    comments.append(text)
    sections = ["\n\n".join(paragraphs)]
    if tables:
        sections.append("## 表格\n\n" + "\n".join(tables))
    if comments:
        sections.append("## 批注\n\n" + "\n".join(comments))
    return Extraction("\n\n".join(item for item in sections if item).strip() + "\n", "docx")


def _epub(path: Path) -> Extraction:
    with zipfile.ZipFile(path) as archive:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = next(node for node in container.iter() if node.tag.endswith("rootfile"))
        opf_name = rootfile.attrib["full-path"]
        opf = ET.fromstring(archive.read(opf_name))
        manifest: dict[str, str] = {}
        for node in opf.iter():
            if node.tag.endswith("item") and "id" in node.attrib and "href" in node.attrib:
                manifest[node.attrib["id"]] = node.attrib["href"]
        spine = [node.attrib["idref"] for node in opf.iter() if node.tag.endswith("itemref")]
        base = PurePosixPath(opf_name).parent
        chapters: list[str] = []
        for item_id in spine:
            href = manifest.get(item_id)
            if not href:
                continue
            chapter_name = str(base / PurePosixPath(href.split("#", 1)[0]))
            parser = _TextHTMLParser()
            parser.feed(archive.read(chapter_name).decode("utf-8", errors="replace"))
            chapter = parser.text().strip()
            if chapter:
                chapters.append(chapter)
        return Extraction("\n\n".join(chapters).strip() + "\n", "epub")


def _pdf(path: Path, work_dir: Path) -> Extraction:
    text_path = work_dir / "pdftotext.txt"
    work_dir.mkdir(parents=True, exist_ok=True)
    _run(["pdftotext", "-layout", str(path), str(text_path)])
    text = text_path.read_text("utf-8", errors="replace") if text_path.exists() else ""
    visible = len(re.sub(r"\s+", "", text))
    if visible >= 80:
        return Extraction(text.strip() + "\n", "pdf_text")
    pages = work_dir / "pages"
    pages.mkdir(exist_ok=True)
    _run(["pdftoppm", "-png", "-r", "200", str(path), str(pages / "page")])
    output: list[str] = []
    for page in sorted(pages.glob("page-*.png")):
        base = work_dir / f"ocr-{page.stem}"
        _run(["tesseract", str(page), str(base), "-l", os.environ.get("GAME_KB_OCR_LANG", "chi_sim+eng")])
        txt = base.with_suffix(".txt")
        output.append(f"## {page.stem}\n\n{txt.read_text('utf-8', errors='replace').strip()}")
    return Extraction("\n\n".join(output).strip() + "\n", "pdf_ocr")


def _image(path: Path, work_dir: Path) -> Extraction:
    work_dir.mkdir(parents=True, exist_ok=True)
    base = work_dir / "ocr"
    _run(["tesseract", str(path), str(base), "-l", os.environ.get("GAME_KB_OCR_LANG", "chi_sim+eng")])
    text = base.with_suffix(".txt").read_text("utf-8", errors="replace")
    return Extraction(text.strip() + "\n", "image_ocr")


def _media(path: Path, work_dir: Path, video: bool) -> Extraction:
    work_dir.mkdir(parents=True, exist_ok=True)
    wav = work_dir / "audio.wav"
    _run(["ffmpeg", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    model = os.environ.get("GAME_KB_WHISPER_MODEL", "base")
    language = os.environ.get("GAME_KB_WHISPER_LANGUAGE", "Chinese")
    _run([
        "whisper", str(wav), "--model", model, "--language", language,
        "--output_format", "json", "--output_dir", str(work_dir), "--fp16", "False",
    ])
    payload_path = work_dir / f"{wav.stem}.json"
    payload = json.loads(payload_path.read_text("utf-8"))
    transcript = []
    lines = []
    for index, segment in enumerate(payload.get("segments", [])):
        item = {
            "segment": index,
            "start": float(segment.get("start", 0)),
            "end": float(segment.get("end", 0)),
            "text": str(segment.get("text", "")).strip(),
        }
        transcript.append(item)
        lines.append(f"[{item['start']:.2f}-{item['end']:.2f}] {item['text']}")
    frames: list[Path] = []
    if video:
        frame_dir = work_dir / "frames"
        frame_dir.mkdir(exist_ok=True)
        _run([
            "ffmpeg", "-y", "-i", str(path), "-vf",
            "select='gt(scene,0.35)+isnan(prev_selected_t)+gte(t-prev_selected_t,60)'",
            "-vsync", "vfr", str(frame_dir / "frame-%06d.jpg"),
        ])
        frames = sorted(frame_dir.glob("frame-*.jpg"))
        for frame in frames:
            base = frame.with_suffix("")
            try:
                _run(["tesseract", str(frame), str(base), "-l", os.environ.get("GAME_KB_OCR_LANG", "chi_sim+eng")])
            except ExtractionError:
                continue
            ocr_path = base.with_suffix(".txt")
            if ocr_path.exists() and ocr_path.stat().st_size:
                lines.append(f"\n[FRAME {frame.name}]\n{ocr_path.read_text('utf-8', errors='replace').strip()}")
    return Extraction("\n".join(lines).strip() + "\n", "video" if video else "audio", transcript, frames)


def extract_file(path: Path, work_dir: Path) -> Extraction:
    suffix = path.suffix.lower()
    text_suffixes = {".md", ".txt", ".csv", ".tsv", ".srt", ".vtt", ".ass", ".yaml", ".yml", ".xml"}
    if suffix in text_suffixes:
        return Extraction(_decode(path), "text")
    if suffix == ".json":
        payload = json.loads(_decode(path))
        return Extraction(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "json")
    if suffix == ".jsonl":
        lines = []
        for raw in _decode(path).splitlines():
            if raw.strip():
                lines.append(json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True))
        return Extraction("\n".join(lines) + "\n", "jsonl")
    if suffix in {".html", ".htm", ".xhtml"}:
        parser = _TextHTMLParser()
        parser.feed(_decode(path))
        return Extraction(parser.text(), "html")
    if suffix == ".docx":
        return _docx(path)
    if suffix == ".epub":
        return _epub(path)
    if suffix == ".pdf":
        return _pdf(path, work_dir)
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}:
        return _image(path, work_dir)
    if suffix in {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".opus", ".aiff", ".aif", ".wma"}:
        return _media(path, work_dir, False)
    if suffix in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}:
        return _media(path, work_dir, True)
    raise ExtractionError(f"不支持的文件格式: {path.name}")
