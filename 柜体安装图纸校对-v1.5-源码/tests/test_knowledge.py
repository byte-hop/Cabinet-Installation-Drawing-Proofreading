from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.knowledge import KnowledgeBase, issue_signature


class KnowledgeBaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.kb = KnowledgeBase(Path(self.temp.name) / "knowledge.db")

    def tearDown(self):
        self.temp.cleanup()

    def issue(self, title="五金 H 用量差 +2 件"):
        return {
            "code": "HW_TOTAL_MISMATCH",
            "title": title,
            "severity": "error",
            "page": 8,
            "step": 5,
        }

    def test_signature_is_stable_and_template_specific(self):
        left = issue_signature("lingkewu", self.issue("五金 H 用量差 +2 件"))
        right = issue_signature("lingkewu", self.issue("  五金 H 用量差   +2 件 "))
        other = issue_signature("aosom24", self.issue("五金 H 用量差 +2 件"))
        self.assertEqual(left, right)
        self.assertNotEqual(left, other)

    def test_dismiss_removes_issue_and_confirm_marks_it(self):
        template = "lingkewu"
        issue = self.issue()
        signature = self.kb.remember_issue(
            template=template,
            template_name="领客屋版式",
            issue=issue,
            decision="dismiss",
        )
        self.assertTrue(signature)
        apply, info = self.kb.make_issue_filter(template)
        self.assertFalse(apply(dict(issue)))
        self.assertEqual(info["dismissed"], 1)

        self.kb.remember_issue(
            template=template,
            template_name="领客屋版式",
            issue=issue,
            decision="confirm",
        )
        apply, info = self.kb.make_issue_filter(template)
        confirmed = dict(issue)
        self.assertTrue(apply(confirmed))
        self.assertEqual(info["confirmed"], 1)
        self.assertEqual(confirmed["knowledge"]["decision"], "confirm")

    def test_cache_roundtrip_and_hit_stats(self):
        payload = {
            "file": {"name": "demo.pdf", "size": 123, "pages": 2},
            "detected": {"template": "yingchi", "template_name": "英驰版式"},
            "pages": [],
            "bom": [],
            "parts": [],
            "overview": [],
            "overview_boxes": [],
            "steps": [],
            "parse_warnings": [],
            "issues": [],
            "checks": [],
            "summary": {},
        }
        self.assertTrue(
            self.kb.save_document(
                "fingerprint",
                payload,
                file_name="demo.pdf",
                template="yingchi",
                template_name="英驰版式",
                page_count=2,
                elapsed_ms=100,
            )
        )
        cached = self.kb.get_document("fingerprint")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["payload"]["file"]["pages"], 2)
        self.kb.mark_cache_hit("fingerprint", 20)
        stats = self.kb.stats()
        self.assertEqual(stats["documents"], 1)
        self.assertEqual(stats["cache_hits"], 1)
        self.assertGreater(stats["speedup_pct"], 0)
        self.assertEqual(self.kb.clear_documents(), 1)
        self.assertIsNone(self.kb.get_document("fingerprint"))

    def test_delete_correction(self):
        signature = self.kb.remember_issue(
            template="aosom24",
            template_name="遨森 24 页版式",
            issue=self.issue("五金 N 用量差 +2 件"),
            decision="dismiss",
        )
        self.assertTrue(self.kb.delete_correction(signature))
        apply, info = self.kb.make_issue_filter("aosom24")
        self.assertTrue(apply(self.issue("五金 N 用量差 +2 件")))
        self.assertEqual(info["dismissed"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
