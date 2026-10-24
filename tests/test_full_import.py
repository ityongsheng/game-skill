from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.helpers import sha256_bytes
from game_kb.core import GameKB


class FullImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.kb = GameKB(root / "kb", root / "repo")
        self.kb.init()

    def test_streaming_full_import_restores_records_and_manifest(self) -> None:
        first = "# 第一份\n\n这里含有 ``` 内嵌短围栏。\n"
        second = "第二份原文\n"
        binary_hash = "a" * 64
        full = Path(self.temp.name) / "FULL.md"
        full.write_text(
            "---\ntext_files: 2\nbinary_inventory_files: 1\n---\n\n"
            "## 二进制文件清单\n\n| path | bytes | sha256 |\n|---|---:|---|\n"
            f"| `video/a.mp4` | 123 | `{binary_hash}` |\n\n"
            "## 项目说明与最终知识层\n\n"
            "### 1. `01_sources/a.md`\n"
            f"- sha256: `{sha256_bytes(first.encode())}`\n- bytes: {len(first.encode())}\n\n"
            "````markdown\n" + first + "````\n\n"
            "### 2. `terms/b.md`\n"
            f"- sha256: `{sha256_bytes(second.encode())}`\n- bytes: {len(second.encode())}\n\n"
            "```markdown\n" + second + "```\n",
            encoding="utf-8",
        )
        report = self.kb.import_full(full)
        self.assertEqual(2, report["text_records"])
        self.assertEqual(1, report["binary_manifest_records"])
        self.assertEqual([], report["hash_errors"])
        self.assertEqual([], report["unparsed_blocks"])
        self.assertEqual(first, (self.kb.data_root / "legacy/01_sources/a.md").read_text("utf-8"))
        manifest = (self.kb.data_root / "legacy/binary_manifest.jsonl").read_text("utf-8")
        self.assertIn("manifest_only", manifest)
        self.assertTrue(self.kb.search("内嵌短围栏", 3))

    def test_malformed_record_is_preserved_in_unparsed(self) -> None:
        full = Path(self.temp.name) / "broken.md"
        full.write_text(
            "---\ntext_files: 1\nbinary_inventory_files: 0\n---\n\n"
            "## 项目说明与最终知识层\n\n"
            "### 1. `broken.md`\n- sha256: `deadbeef`\n- bytes: 2\n\n```markdown\n内容\n",
            encoding="utf-8",
        )
        report = self.kb.import_full(full)
        self.assertEqual(0, report["text_records"])
        self.assertEqual(1, len(report["unparsed_blocks"]))
        self.assertTrue(Path(report["unparsed_blocks"][0]).is_file())
        rebuilt = self.kb.build_full().read_text("utf-8")
        self.assertIn("unparsed/full-block-1.md", rebuilt)
        self.assertIn("broken.md", rebuilt)

    def test_declared_byte_count_beats_ambiguous_fences_and_accepts_json(self) -> None:
        markdown = "# 主题\n\n```\n内部代码围栏\n```\n"
        payload = '{"观点": "JSON 块"}\n'
        full = Path(self.temp.name) / "ambiguous.md"
        full.write_text(
            "---\ntext_files: 2\nbinary_inventory_files: 0\n---\n\n"
            "## 项目说明与最终知识层\n\n"
            "### 1. `one.md`\n"
            f"- sha256: `{sha256_bytes(markdown.encode())}`\n- bytes: {len(markdown.encode())}\n\n"
            "```markdown\n" + markdown + "```\n\n"
            "### 2. `two.json`\n"
            f"- sha256: `{sha256_bytes(payload.encode())}`\n- bytes: {len(payload.encode())}\n\n"
            "```json\n" + payload + "```\n",
            encoding="utf-8",
        )
        report = self.kb.import_full(full)
        self.assertEqual(2, report["text_records"])
        self.assertEqual([], report["hash_errors"])
        self.assertEqual(payload, (self.kb.data_root / "legacy/two.json").read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
