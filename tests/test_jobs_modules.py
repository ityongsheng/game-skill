from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from game_kb.core import GameKB
from game_kb.errors import ValidationError


class JobAndModuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.data = root / "kb"
        self.repo = root / "repo"
        (self.repo / "modules").mkdir(parents=True)
        (self.repo / "modules/existing.md").write_text(
            "---\nmodule_id: existing\ntitle: 既有模块\ntriggers: [筛选, 冲突]\npriority: 10\n---\n\n"
            "# 既有模块\n\n旧观点。\n",
            "utf-8",
        )
        self.kb = GameKB(self.data, self.repo)
        self.kb.init()
        source = root / "source.txt"
        source.write_text("来源原文说：观点甲。另一个段落说：观点乙。", "utf-8")
        self.kb.update(source)

    def test_next_job_is_resumable_and_bounded(self) -> None:
        job = self.kb.next_job(max_chars=20)
        self.assertLessEqual(len(job["text"]), 20)
        again = self.kb.next_job(max_chars=20)
        self.assertEqual(job["job_id"], again["job_id"])
        self.assertEqual("game-kb-claim-v1", job["output_schema"]["$id"])

    def test_accept_rejects_quote_not_in_source(self) -> None:
        job = self.kb.next_job(max_chars=200)
        with self.assertRaises(ValidationError):
            self.kb.accept_job(
                job["job_id"],
                {"claims": [{"claim_type": "direct", "claim": "伪造", "quote": "不存在的引文"}]},
            )

    def test_conflicting_claims_are_both_preserved(self) -> None:
        job = self.kb.next_job(max_chars=200)
        self.kb.accept_job(
            job["job_id"],
            {"claims": [
                {"claim_type": "direct", "claim": "观点甲成立", "quote": "观点甲", "module_id": "existing", "locator": "paragraph:1"},
                {"claim_type": "reported", "claim": "观点乙与甲冲突", "quote": "观点乙", "module_id": "existing", "locator": "paragraph:2"},
            ]},
        )
        text = (self.repo / "modules/existing.md").read_text("utf-8")
        self.assertIn("观点甲成立", text)
        self.assertIn("观点乙与甲冲突", text)
        self.assertIn("direct", text)
        self.assertIn("reported", text)

    def test_unknown_module_is_created_without_forced_alignment(self) -> None:
        job = self.kb.next_job(max_chars=200)
        self.kb.accept_job(
            job["job_id"],
            {"claims": [{
                "claim_type": "inference", "claim": "一个无法归入旧体系的新主题",
                "quote": "另一个段落", "module_id": "new-topic", "module_title": "新主题",
                "triggers": ["新主题", "独立观点"],
            }]},
        )
        created = self.repo / "modules/new-topic.md"
        self.assertTrue(created.is_file())
        self.assertIn("无法归入旧体系", created.read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
