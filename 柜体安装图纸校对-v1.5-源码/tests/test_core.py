from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.excel_export import build_report
from core.pdf_parser import parse_pdf
from core.rules import check_document

SAMPLE = ROOT / "示例" / "英驰.pdf"


def issue_codes(result):
    return {issue["code"] for issue in result["issues"]}


class SampleDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = parse_pdf(str(SAMPLE), "英驰.pdf")
        cls.result = check_document(cls.model)

    def fresh_model(self):
        return copy.deepcopy(self.model)

    def test_sample_structure(self):
        self.assertEqual(self.model.page_count, 19)
        self.assertEqual(len(self.model.bom), 18)
        self.assertEqual(len(self.model.parts), 17)
        self.assertEqual(len(self.model.steps), 25)
        self.assertEqual(sum(item.qty for item in self.model.bom.values()), 410)
        self.assertEqual(sum(item.spare for item in self.model.bom.values()), 15)
        self.assertEqual(self.model.detected["bom_page"], 4)
        self.assertEqual(self.model.detected["parts_page"], 5)
        self.assertEqual(self.model.overview, list(range(1, 18)))

    def test_sample_passes_with_two_infos(self):
        self.assertEqual(self.result["summary"]["status"], "pass")
        self.assertEqual(self.result["summary"]["errors"], 0)
        self.assertEqual(self.result["summary"]["warnings"], 0)
        self.assertEqual(self.result["summary"]["infos"], 2)
        self.assertEqual(self.result["summary"]["hardware_units"], 410)
        self.assertEqual(len(self.result["checks"]), 9)
        self.assertEqual(issue_codes(self.result), {"HW_TABLE_NOT_DRAWN", "PART_DUP_CALLOUT"})

    def test_missing_hardware_quantity_reports_total_mismatch(self):
        model = self.fresh_model()
        step = next(item for item in model.steps if item.no == 3)
        entry = next(item for item in step.hardware if item["code"] == "A")
        entry["qty"] = 36
        result = check_document(model)
        self.assertIn("HW_TOTAL_MISMATCH", issue_codes(result))
        mismatch = next(item for item in result["issues"] if item["code"] == "HW_TOTAL_MISMATCH")
        self.assertIn("A", mismatch["title"])
        self.assertIn("4", mismatch["title"])

    def test_unknown_hardware_code_is_rejected(self):
        model = self.fresh_model()
        step = next(item for item in model.steps if item.no == 4)
        step.hardware.append({"code": "Z", "qty": 1, "box": [300, 300, 320, 320]})
        result = check_document(model)
        self.assertIn("HW_UNKNOWN", issue_codes(result))
        self.assertEqual(result["summary"]["status"], "fail")

    def test_unknown_part_number_is_rejected(self):
        model = self.fresh_model()
        step = next(item for item in model.steps if item.no == 3)
        step.panels.append({"no": 18, "box": [300, 300, 320, 320]})
        result = check_document(model)
        self.assertIn("PART_UNKNOWN", issue_codes(result))
        self.assertEqual(result["summary"]["status"], "fail")

    def test_repeat_step_cannot_use_single_part_eight_times(self):
        model = self.fresh_model()
        step = next(item for item in model.steps if item.no == 3)
        self.assertEqual(step.repeat, 8)
        step.panels.append({"no": 7, "box": [300, 300, 320, 320]})
        result = check_document(model)
        self.assertIn("PART_REPEAT_IMPOSSIBLE", issue_codes(result))
        self.assertEqual(result["summary"]["status"], "fail")

    def test_deleted_step_reports_gap(self):
        model = self.fresh_model()
        model.steps = [step for step in model.steps if step.no != 12]
        result = check_document(model)
        self.assertIn("STEP_GAP", issue_codes(result))

    def test_excel_report_contains_six_sheets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "report.xlsx"
            build_report(self.model, self.result, str(target))
            self.assertGreater(target.stat().st_size, 5000)
            from openpyxl import load_workbook

            workbook = load_workbook(target)
            self.assertEqual(
                workbook.sheetnames,
                ["校验总览", "五金核对", "板件核对", "步骤明细", "问题清单", "检查说明"],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

class Aosom1145Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = parse_pdf(str(ROOT / "tests" / "fixtures" / "1145.pdf"), "1145.pdf")
        cls.result = check_document(cls.model)

    def test_template_and_structure(self):
        self.assertEqual(self.model.detected["template"], "1145")
        self.assertEqual(self.model.page_count, 15)
        self.assertEqual(len(self.model.bom), 11)
        self.assertEqual(len(self.model.parts), 9)
        self.assertEqual(len(self.model.steps), 14)
        self.assertEqual(sum(item.qty for item in self.model.bom.values()), 88)
        self.assertEqual(sum(item.spare for item in self.model.bom.values()), 8)
        self.assertEqual(sum(item.qty for item in self.model.parts.values()), 14)
        self.assertEqual(self.model.overview, list(range(1, 10)))

    def test_step_tables_and_totals(self):
        step1 = next(step for step in self.model.steps if step.no == 1)
        self.assertEqual(
            {(entry["code"], entry["qty"]) for entry in step1.hardware},
            {("C", 2), ("E", 3)},
        )
        self.assertEqual(step1.panel_nos, [4, 6])
        self.assertEqual(sum(entry["qty"] for step in self.model.steps for entry in step.hardware), 88)
        self.assertEqual(sum(1 for step in self.model.steps if step.hardware), 11)
        self.assertEqual(self.result["summary"]["status"], "pass")
        self.assertEqual(self.result["summary"]["errors"], 0)
        self.assertEqual(issue_codes(self.result), {"HW_TABLE_NOT_DRAWN"})

    def test_pure_assembly_steps_have_no_hardware(self):
        for step_no in (6, 11, 12):
            step = next(step for step in self.model.steps if step.no == step_no)
            self.assertEqual(step.hardware, [])


class Aosom1235Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = parse_pdf(str(ROOT / "tests" / "fixtures" / "1235.pdf"), "1235.pdf")
        cls.result = check_document(cls.model)

    def test_template_and_structure(self):
        self.assertEqual(self.model.detected["template"], "1235")
        self.assertEqual(self.model.page_count, 21)
        self.assertEqual(len(self.model.bom), 11)
        self.assertEqual(len(self.model.parts), 16)
        self.assertEqual(len(self.model.steps), 19)
        self.assertEqual(sum(item.qty for item in self.model.bom.values()), 151)
        self.assertEqual(sum(item.spare for item in self.model.bom.values()), 12)
        self.assertEqual(sum(item.qty for item in self.model.parts.values()), 19)
        self.assertEqual(self.model.overview, list(range(1, 17)))
        self.assertEqual(
            [item.qty for item in sorted(self.model.parts.values(), key=lambda item: item.no)],
            [1, 1, 1, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 1, 2],
        )

    def test_step_counts_are_single_unit_quantities(self):
        step1 = next(step for step in self.model.steps if step.no == 1)
        self.assertEqual(
            {(entry["code"], entry["qty"]) for entry in step1.hardware},
            {("C", 2), ("D", 2), ("E", 2)},
        )
        self.assertEqual(step1.repeat, 1)
        step12 = next(step for step in self.model.steps if step.no == 12)
        self.assertEqual(step12.hardware, [])
        self.assertNotIn("CR", self.model.bom)

    def test_real_quantity_differences_are_reported(self):
        mismatches = {
            issue["title"].split()[1]: issue["evidence"]["diff"]
            for issue in self.result["issues"]
            if issue["code"] == "HW_TOTAL_MISMATCH"
        }
        self.assertEqual(mismatches, {"C": -4, "E": -20, "F": -4, "G": -2, "J": 2})
        self.assertEqual(self.result["summary"]["status"], "fail")
        self.assertEqual(self.result["summary"]["errors"], 5)
        self.assertEqual(self.result["summary"]["warnings"], 0)
        self.assertNotIn("PART_UNKNOWN", issue_codes(self.result))

    def test_excel_report_for_aosom_template(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "aosom.xlsx"
            build_report(self.model, self.result, str(target))
            self.assertGreater(target.stat().st_size, 5000)
            from openpyxl import load_workbook

            workbook = load_workbook(target)
            self.assertEqual(len(workbook.sheetnames), 6)
            self.assertIn("Aosom 1235", workbook["校验总览"]["A2"].value)


class LingkewuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = parse_pdf(str(ROOT / "示例" / "领客屋.pdf"), "领客屋.pdf")
        cls.result = check_document(cls.model)

    def test_template_and_structure(self):
        self.assertEqual(self.model.detected["template"], "lingkewu")
        self.assertEqual(self.model.page_count, 22)
        self.assertEqual(len(self.model.bom), 18)
        self.assertEqual(len(self.model.parts), 17)
        self.assertEqual(len(self.model.steps), 25)
        self.assertEqual(sum(item.qty for item in self.model.bom.values()), 166)
        self.assertEqual(sum(item.qty for item in self.model.parts.values()), 22)
        self.assertEqual(self.model.overview, list(range(1, 18)))
        self.assertEqual(self.model.detected["table_count"], 22)

    def test_two_letter_hardware_and_variant_callouts(self):
        self.assertTrue({"CL", "CR", "DL", "DR"}.issubset(self.model.bom))
        step4 = next(step for step in self.model.steps if step.no == 4)
        step5 = next(step for step in self.model.steps if step.no == 5)
        self.assertIn(("CL", 2), {(entry["code"], entry["qty"]) for entry in step4.hardware})
        self.assertIn(("CR", 2), {(entry["code"], entry["qty"]) for entry in step5.hardware})
        step17 = next(step for step in self.model.steps if step.no == 17)
        step18 = next(step for step in self.model.steps if step.no == 18)
        self.assertEqual(step17.callout_counts.get("F"), 2)
        self.assertEqual(step18.callout_counts.get("H"), 4)

    def test_real_quantity_differences_are_reported(self):
        mismatches = {
            issue["title"].split()[1]: issue["evidence"]["diff"]
            for issue in self.result["issues"]
            if issue["code"] == "HW_TOTAL_MISMATCH"
        }
        self.assertEqual(mismatches, {"H": 2})
        self.assertIn("HW_UNUSED", issue_codes(self.result))
        self.assertEqual(self.result["summary"]["errors"], 2)
        self.assertEqual(self.result["summary"]["infos"], 0)
        self.assertNotIn("HW_UNKNOWN", issue_codes(self.result))


class Aosom24Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = parse_pdf(str(ROOT / "示例" / "遨森.pdf"), "遨森.pdf")
        cls.result = check_document(cls.model)

    def test_template_and_structure(self):
        self.assertEqual(self.model.detected["template"], "aosom24")
        self.assertEqual(self.model.page_count, 24)
        self.assertEqual(len(self.model.bom), 14)
        self.assertEqual(len(self.model.parts), 24)
        self.assertEqual(len(self.model.steps), 32)
        self.assertEqual(sum(item.qty for item in self.model.bom.values()), 246)
        self.assertEqual(sum(item.spare for item in self.model.bom.values()), 13)
        self.assertEqual(sum(item.qty for item in self.model.parts.values()), 36)
        self.assertEqual(self.model.overview, list(range(1, 25)))
        self.assertEqual(self.model.detected["table_count"], 30)

    def test_hardware_letters_follow_the_printed_labels(self):
        self.assertEqual(set(self.model.bom), set("ABCDEFGHIJKLMN"))
        self.assertEqual(self.model.bom["I"].qty, 16)
        step1 = next(step for step in self.model.steps if step.no == 1)
        self.assertEqual(
            {(entry["code"], entry["qty"]) for entry in step1.hardware},
            {("A", 10), ("E", 10)},
        )

    def test_real_quantity_difference_is_reported(self):
        mismatches = {
            issue["title"].split()[1]: issue["evidence"]["diff"]
            for issue in self.result["issues"]
            if issue["code"] == "HW_TOTAL_MISMATCH"
        }
        self.assertEqual(mismatches, {"N": 2})
        self.assertEqual(self.result["summary"]["errors"], 1)
        self.assertNotIn("PART_UNKNOWN", issue_codes(self.result))


class YingchiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = parse_pdf(str(ROOT / "示例" / "英驰.pdf"), "英驰.pdf")
        cls.result = check_document(cls.model)

    def test_template_and_structure(self):
        self.assertEqual(self.model.detected["template"], "1041")
        self.assertEqual(self.model.detected["template_name"], "英驰版式")
        self.assertEqual(self.model.page_count, 19)
        self.assertEqual(len(self.model.bom), 18)
        self.assertEqual(len(self.model.parts), 17)
        self.assertEqual(len(self.model.steps), 25)
        self.assertEqual(sum(item.qty for item in self.model.bom.values()), 410)
        self.assertEqual(self.result["summary"]["errors"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
