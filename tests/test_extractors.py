from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import write_docx, write_epub
from game_kb.extractors import extract_file


class ExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_docx_preserves_paragraphs_tables_and_comments(self) -> None:
        path = self.root / "sample.docx"
        write_docx(path, ["标题", "正文段落"], ["表格甲", "表格乙"])
        result = extract_file(path, self.root / "work")
        self.assertIn("标题", result.text)
        self.assertIn("正文段落", result.text)
        self.assertIn("表格甲 | 表格乙", result.text)
        self.assertIn("批注意见", result.text)

    def test_epub_follows_spine_order(self) -> None:
        path = self.root / "sample.epub"
        write_epub(path)
        result = extract_file(path, self.root / "work")
        self.assertLess(result.text.index("第一章"), result.text.index("第二章"))

    def test_json_keeps_structure(self) -> None:
        path = self.root / "sample.json"
        path.write_text(json.dumps({"观点": ["甲", "乙"]}, ensure_ascii=False), "utf-8")
        result = extract_file(path, self.root / "work")
        self.assertIn('"观点"', result.text)
        self.assertIn('"甲"', result.text)


if __name__ == "__main__":
    unittest.main()
