from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from game_kb.core import GameKB
from game_kb.routes import generate_routes


class RoutesReleaseVerifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.data = root / "kb"
        self.repo = root / "repo"
        (self.repo / "modules").mkdir(parents=True)
        (self.repo / "index").mkdir()
        (self.repo / "modules/sample.md").write_text(
            "---\nmodule_id: sample\ntitle: 样例模块\ntriggers: [样例, 测试]\npriority: 9\n---\n\n"
            "# 样例模块\n\n内容。\n",
            "utf-8",
        )
        (self.repo / "SKILL.md").write_text("---\nname: game-skill\ndescription: test\n---\n", "utf-8")
        (self.repo / "VERSION").write_text("4.0.0\n", "utf-8")
        self.kb = GameKB(self.data, self.repo)
        self.kb.init()
        source = root / "note.txt"
        source.write_text("用于构建完整导出的文本。", "utf-8")
        self.kb.update(source)

    def test_routes_are_generated_from_module_frontmatter(self) -> None:
        catalog = generate_routes(self.repo)
        self.assertEqual("sample", catalog["modules"][0]["id"])
        generated = (self.repo / "skill.yaml").read_text("utf-8")
        self.assertIn("modules/sample.md", generated)
        self.assertIn("样例", generated)
        machine = json.loads((self.repo / "index/module_catalog.json").read_text("utf-8"))
        self.assertEqual(catalog, machine)

    def test_module_layer_is_searchable_and_readable(self) -> None:
        hits = self.kb.search("样例模块", top_k=3)
        module_hit = next(item for item in hits if item["chunk_id"].startswith("module:"))
        self.assertIn("样例模块", module_hit["snippet"])
        content = self.kb.read(module_hit["chunk_id"])
        self.assertIn("# 样例模块", content["text"])

    def test_build_full_and_verify(self) -> None:
        full = self.kb.build_full()
        self.assertTrue(full.is_file())
        self.assertIn("用于构建完整导出的文本", full.read_text("utf-8"))
        report = self.kb.verify(full=True)
        self.assertEqual("ok", report["status"])
        self.assertEqual([], report["errors"])

    def test_publish_dry_run_builds_local_assets_only(self) -> None:
        release = self.kb.publish(dry_run=True)
        self.assertEqual("dry_run", release["status"])
        self.assertEqual("4.0.1", release["version"])
        assets = [Path(item["path"]) for item in release["assets"]]
        self.assertTrue(all(path.is_file() for path in assets))
        gz = next(path for path in assets if path.name.endswith("FULL.md.gz"))
        with gzip.open(gz, "rt", encoding="utf-8") as handle:
            self.assertIn("用于构建完整导出的文本", handle.read())
        manifest_path = next(path for path in assets if path.name == "release_manifest.json")
        manifest = json.loads(manifest_path.read_text("utf-8"))
        self.assertNotIn("path", manifest["assets"][0])
        self.assertFalse((self.repo / ".git").exists())


if __name__ == "__main__":
    unittest.main()
