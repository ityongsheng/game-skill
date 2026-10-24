from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_docx(path: Path, paragraphs: list[str], table_cells: list[str] | None = None) -> None:
    table_cells = table_cells or []
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>' for text in paragraphs)
    if table_cells:
        cells = "".join(
            f'<w:tc><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:tc>' for text in table_cells
        )
        body += f"<w:tbl><w:tr>{cells}</w:tr></w:tbl>"
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>'
    )
    comments = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:comments xmlns:w="{ns}"><w:comment w:id="0">'
        '<w:p><w:r><w:t>批注意见</w:t></w:r></w:p></w:comment></w:comments>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/comments.xml", comments)


def write_epub(path: Path) -> None:
    container = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
    opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="c2" href="two.xhtml" media-type="application/xhtml+xml"/>
    <item id="c1" href="one.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""
    one = '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>第一章</h1><p>先出现的内容</p></body></html>'
    two = '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>第二章</h1><p>后出现的内容</p></body></html>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/one.xhtml", one)
        archive.writestr("OEBPS/two.xhtml", two)


def dump_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
