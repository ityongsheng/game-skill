from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import REPO_ROOT
from game_kb.core import GameKB


class StorageAndIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.data_root = root / "kb"
        self.repo_root = root / "repo"
        (self.repo_root / "modules").mkdir(parents=True)
        self.kb = GameKB(self.data_root, self.repo_root)
        self.kb.init()

    def test_init_creates_declared_layout(self) -> None:
        for relative in (
            "inbox", "raw/sha256", "sources", "aggregates", "index/vectors",
            "state", "reports", "releases", "unparsed",
        ):
            self.assertTrue((self.data_root / relative).is_dir(), relative)
        self.assertTrue((self.data_root / "index/kb.sqlite3").is_file())
        self.assertTrue((self.data_root / "state/jobs.sqlite3").is_file())

    def test_duplicate_content_reuses_raw_but_keeps_aliases(self) -> None:
        first = Path(self.temp.name) / "first.txt"
        second = Path(self.temp.name) / "renamed.txt"
        first.write_text("同一份原始资料", encoding="utf-8")
        second.write_text("同一份原始资料", encoding="utf-8")
        one = self.kb.update(first)
        two = self.kb.update(second)
        self.assertEqual(one["sources"][0]["source_id"], two["sources"][0]["source_id"])
        raw_files = [p for p in (self.data_root / "raw/sha256").rglob("*") if p.is_file()]
        self.assertEqual(1, len(raw_files))
        source_id = one["sources"][0]["source_id"]
        metadata = json.loads((self.data_root / "sources" / source_id / "source.yaml").read_text("utf-8"))
        self.assertEqual({"first.txt", "renamed.txt"}, {item["name"] for item in metadata["aliases"]})

    def test_text_update_is_searchable_and_readable(self) -> None:
        source = Path(self.temp.name) / "note.md"
        source.write_text("# 新理论\n\n独立观点：筛选不等于迎合。\n", encoding="utf-8")
        result = self.kb.update(source)
        self.assertEqual("complete", result["status"])
        hits = self.kb.search("筛选 迎合", top_k=5)
        self.assertTrue(hits)
        self.assertIn("筛选不等于迎合", hits[0]["snippet"])
        chunk = self.kb.read(hits[0]["chunk_id"])
        self.assertEqual(hits[0]["chunk_id"], chunk["chunk_id"])
        self.assertIn("独立观点", chunk["text"])

    def test_new_version_creates_new_source(self) -> None:
        source = Path(self.temp.name) / "versioned.txt"
        source.write_text("第一版", encoding="utf-8")
        one = self.kb.update(source)["sources"][0]["source_id"]
        source.write_text("第二版", encoding="utf-8")
        two = self.kb.update(source)["sources"][0]["source_id"]
        self.assertNotEqual(one, two)

    def test_failed_source_can_retry_via_supported_alias(self) -> None:
        unsupported = Path(self.temp.name) / "retry.unsupported"
        supported = Path(self.temp.name) / "retry.txt"
        unsupported.write_text("失败后可恢复的同一内容", encoding="utf-8")
        supported.write_text("失败后可恢复的同一内容", encoding="utf-8")
        failed = self.kb.update(unsupported)
        self.assertEqual("failed", failed["sources"][0]["status"])
        retried = self.kb.update(supported)
        self.assertEqual("complete", retried["sources"][0]["status"])
        self.assertEqual(failed["sources"][0]["source_id"], retried["sources"][0]["source_id"])


if __name__ == "__main__":
    unittest.main()
