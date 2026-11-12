from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .pdf_parser import DocumentModel


NAVY = "13233A"
ACCENT = "2F6BFF"
LIGHT = "F2F5FA"
GREEN = "E6F7F0"
GREEN_TEXT = "0A7A55"
AMBER = "FFF4E5"
AMBER_TEXT = "A85B00"
RED = "FDECEC"
RED_TEXT = "C0353A"
BLUE = "EAF2FF"
BLUE_TEXT = "2457B8"
GREY_TEXT = "5E6C80"

THIN = Side(style="thin", color="DCE3ED")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def build_report(model: DocumentModel, result: dict[str, Any], output_path: str) -> str:
    wb = Workbook()
    wb.properties.creator = "柜图校对"
    wb.properties.title = "柜体安装图校验报告"

    summary = result["summary"]
    issues = result["issues"]
    checks = result["checks"]

    ws = wb.active
    ws.title = "校验总览"
    _build_overview_sheet(ws, model, summary, checks, issues)

    _build_hardware_sheet(wb.create_sheet("五金核对"), model, issues)
    _build_parts_sheet(wb.create_sheet("板件核对"), model, issues)
    _build_steps_sheet(wb.create_sheet("步骤明细"), model, issues)
    _build_issues_sheet(wb.create_sheet("问题清单"), issues)
    _build_notes_sheet(wb.create_sheet("检查说明"))

    wb.save(output_path)
    return output_path


def _status_label(status: str) -> str:
    return {
        "pass": "通过",
        "warn": "待确认",
        "fail": "不通过",
        "review": "有提示",
        "error": "错误",
        "info": "提示",
    }.get(status, status)


def _status_style(status: str) -> tuple[PatternFill, Font]:
    if status in {"pass"}:
        return PatternFill("solid", fgColor=GREEN), Font(color=GREEN_TEXT, bold=True)
    if status in {"warn", "review"}:
        return PatternFill("solid", fgColor=AMBER), Font(color=AMBER_TEXT, bold=True)
    if status in {"fail", "error"}:
        return PatternFill("solid", fgColor=RED), Font(color=RED_TEXT, bold=True)
    return PatternFill("solid", fgColor=BLUE), Font(color=BLUE_TEXT, bold=True)


def _title(ws, text: str, span: int, row: int = 1) -> None:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
    cell = ws.cell(row=row, column=1, value=text)
    cell.font = Font(size=18, bold=True, color=NAVY)
    cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[row].height = 32


def _subtitle(ws, text: str, span: int, row: int) -> None:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
    cell = ws.cell(row=row, column=1, value=text)
    cell.font = Font(size=10, color=GREY_TEXT)
    cell.alignment = Alignment(vertical="center", wrap_text=True)


def _header(ws, row: int, headers: list[str]) -> None:
    for column, text in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=column, value=text)
        cell.font = Font(bold=True, color="FFFFFF", size=10)
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER
    ws.row_dimensions[row].height = 26


def _body(ws, row: int, values: list[Any], status: str | None = None) -> None:
    for column, value in enumerate(values, start=1):
        cell = ws.cell(row=row, column=column, value=value)
        cell.font = Font(size=10, color="1C2A3A")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cell.border = BORDER
        cell.fill = PatternFill("solid", fgColor="FFFFFF" if row % 2 else "FAFBFD")


def _paint_cell(ws, row: int, column: int, status: str) -> None:
    fill, font = _status_style(status)
    cell = ws.cell(row=row, column=column)
    cell.fill = fill
    cell.font = Font(size=10, bold=True, color=font.color)


def _build_overview_sheet(ws, model: DocumentModel, summary: dict[str, Any], checks, issues) -> None:
    ws.sheet_view.showGridLines = False
    widths = [18, 24, 16, 16, 42]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width
    _title(ws, "柜体安装图校验报告", 5)
    _subtitle(
        ws,
        f"文件：{model.file_name}    识别模板：{model.detected.get('template_name', '未标注')}    "
        f"PDF 共 {model.page_count} 页    生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        5,
        2,
    )

    status = summary["status"]
    fill, font = _status_style(status)
    cell = ws.cell(row=4, column=1, value="总体结论")
    cell.font = Font(bold=True, color=NAVY)
    result_cell = ws.cell(row=4, column=2, value=_status_label(status))
    result_cell.fill = fill
    result_cell.font = Font(size=12, bold=True, color=font.color)
    result_cell.alignment = Alignment(horizontal="center")
    ws.merge_cells(start_row=4, start_column=2, end_row=4, end_column=3)
    ws.cell(
        row=4,
        column=4,
        value=f"错误 {summary['errors']} · 待确认 {summary['warnings']} · 提示 {summary['infos']}",
    ).font = Font(size=10, color=GREY_TEXT)

    stats = [
        ("五金种类", summary["hardware_kinds"], "类"),
        ("五金安装用量", summary["hardware_units"], "件"),
        ("五金清单需求", summary["bom_units"], "件"),
        ("备用五金", summary["spare_units"], "件"),
        ("板件种类", summary["part_kinds"], "类"),
        ("板件总数量", summary["part_units"], "件"),
        ("安装步骤", summary["step_count"], "步"),
        ("步骤用量表", summary["table_count"], "张"),
    ]
    row = 6
    for index, (label, value, unit) in enumerate(stats):
        column = 1 + (index % 4) * 2
        ws.cell(row=row, column=column, value=label).font = Font(size=10, color=GREY_TEXT)
        value_cell = ws.cell(row=row, column=column + 1, value=f"{value} {unit}")
        value_cell.font = Font(size=13, bold=True, color=NAVY)
        value_cell.alignment = Alignment(horizontal="left")

    _header(ws, 9, ["检查项", "结果", "状态", "问题数", "说明"])
    for offset, check in enumerate(checks, start=10):
        values = [check["name"], check["summary"], _status_label(check["status"]), check["issue_count"], check["hint"]]
        _body(ws, offset, values)
        _paint_cell(ws, offset, 3, check["status"])

    row = 10 + len(checks) + 2
    ws.cell(row=row, column=1, value="识别到的图纸结构").font = Font(bold=True, size=12, color=NAVY)
    structure = [
        ("识别模板", f"{model.detected.get('template_name', '未标注')}（{model.detected.get('template', 'unknown')}）"),
        ("五金清单", f"PDF 第 {model.detected['bom_page']} 页（图册 {model.detected['bom_page_label']}）"),
        ("板件清单", f"PDF 第 {model.detected['parts_page']} 页（图册 {model.detected['parts_page_label']}）"),
        ("安装步骤", f"第 {model.detected['step_min']}–{model.detected['step_max']} 步，共 {summary['step_count']} 步"),
        ("五金用量表", f"{summary['table_count']} 张，其中 {summary['repeat_steps']} 个步骤按 ×N 重复"),
        ("识别方式", "PDF 文字层 + 版面规则，本地离线运行"),
    ]
    for index, (label, value) in enumerate(structure, start=row + 1):
        ws.cell(row=index, column=1, value=label).font = Font(size=10, color=GREY_TEXT)
        ws.merge_cells(start_row=index, start_column=2, end_row=index, end_column=5)
        ws.cell(row=index, column=2, value=value).font = Font(size=10, color="1C2A3A")

    row = row + len(structure) + 2
    if issues:
        ws.cell(row=row, column=1, value="问题摘要").font = Font(bold=True, size=12, color=NAVY)
        for index, issue in enumerate(issues[:12], start=row + 1):
            ws.cell(row=index, column=1, value=_status_label(issue["severity"])).font = Font(
                size=10, bold=True, color=_status_style(issue["severity"])[1].color
            )
            ws.merge_cells(start_row=index, start_column=2, end_row=index, end_column=5)
            ws.cell(row=index, column=2, value=issue["title"]).font = Font(size=10, color="1C2A3A")
    else:
        ws.cell(row=row, column=1, value="未发现需要修正的问题。").font = Font(size=11, color=GREEN_TEXT, bold=True)


def _build_hardware_sheet(ws, model: DocumentModel, issues) -> None:
    ws.sheet_view.showGridLines = False
    widths = [8, 10, 8, 10, 10, 10, 10, 44, 10]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width
    _title(ws, "五金核对", 9)
    _subtitle(ws, "清单需求为不含备用的安装数量；供应合计 = 清单需求 + 备用。差额 = 步骤合计 − 清单需求。", 9, 2)
    _header(ws, 4, ["编号", "清单需求", "备用", "供应合计", "步骤合计", "差额", "使用步骤", "状态", "备注"])

    usage: dict[str, list[str]] = defaultdict(list)
    for step in model.steps:
        for entry in step.hardware:
            usage[entry["code"]].append(f"S{step.no}" + (f"×{step.repeat}" if step.repeat > 1 else ""))

    row = 5
    for item in sorted(model.bom.values(), key=lambda x: (len(x.code), x.code)):
        used = sum(entry["qty"] for step in model.steps for entry in step.hardware if entry["code"] == item.code)
        diff = used - item.qty
        if diff == 0:
            status = "pass"
            note = "与清单需求一致" + (f"，备用 {item.spare} 件" if item.spare else "")
        else:
            status = "fail"
            note = f"步骤合计与清单需求相差 {diff:+d} 件"
        values = [
            item.code,
            item.qty,
            item.spare,
            item.qty + item.spare,
            used,
            diff,
            "、".join(usage.get(item.code, [])) or "未使用",
            _status_label(status),
            note,
        ]
        _body(ws, row, values)
        _paint_cell(ws, row, 8, status)
        row += 1

    row += 1
    ws.cell(row=row, column=1, value="未在清单中的五金编号").font = Font(bold=True, size=11, color=NAVY)
    row += 1
    extras = sorted({issue["title"].split()[-1] for issue in issues if issue["code"] == "HW_UNKNOWN"})
    if extras:
        for code in extras:
            ws.cell(row=row, column=1, value=code).font = Font(bold=True, color=RED_TEXT)
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=9)
            ws.cell(row=row, column=2, value="步骤中使用了该编号，但五金清单没有列出。").font = Font(size=10, color=RED_TEXT)
            row += 1
    else:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=9)
        ws.cell(row=row, column=1, value="无。所有步骤五金编号均存在于清单。").font = Font(size=10, color=GREEN_TEXT)


def _build_parts_sheet(ws, model: DocumentModel, issues) -> None:
    ws.sheet_view.showGridLines = False
    widths = [8, 10, 32, 12, 12, 12, 12, 44]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width
    _title(ws, "板件核对", 8)
    _subtitle(ws, "板件清单是按全柜统计；重复步骤按 ×N 折算每套用量。同一板件在同一张步骤图中多次标注时列为“待确认”。", 8, 2)
    _header(ws, 4, ["编号", "清单数量", "引用步骤", "首次出现", "同图最多标注", "每套折算", "总览", "状态"])

    refs: dict[int, list[int]] = defaultdict(list)
    max_marks: dict[int, tuple[int, int]] = {}
    for step in model.steps:
        counts = Counter(int(panel["no"]) for panel in step.panels)
        for no, count in counts.items():
            refs[no].append(step.no)
            if no not in max_marks or count > max_marks[no][0]:
                max_marks[no] = (count, step.no)
    overview = set(model.overview)

    row = 5
    for item in sorted(model.parts.values(), key=lambda p: p.no):
        repeat = next((s.repeat for s in model.steps if item.no in s.panel_nos and s.repeat > 1), 1)
        per_unit = item.qty / repeat if repeat else item.qty
        max_count, max_step = max_marks.get(item.no, (0, 0))
        status = "pass"
        note = "编号有效"
        if item.no not in refs:
            status = "warn"
            note = "未在任何步骤中引用"
        elif max_count > per_unit:
            status = "warn"
            note = f"步骤 {max_step} 同图标注 {max_count} 次，可能为多视图"
        values = [
            item.no,
            item.qty,
            "、".join(f"S{n}" for n in refs.get(item.no, [])) or "—",
            f"S{refs[item.no][0]}" if refs.get(item.no) else "—",
            max_count if max_count else "—",
            f"{per_unit:g}" if repeat > 1 else "—",
            "有" if item.no in overview else "无",
            f"{_status_label(status)}：{note}",
        ]
        _body(ws, row, values)
        _paint_cell(ws, row, 8, status)
        row += 1


def _build_steps_sheet(ws, model: DocumentModel, issues) -> None:
    ws.sheet_view.showGridLines = False
    widths = [7, 9, 10, 8, 30, 42, 20, 30]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width
    _title(ws, "步骤明细", 8)
    _subtitle(ws, "每张步骤卡的五金用量表已折算每套用量；“备注”列出该步骤关联的核对提示。", 8, 2)
    _header(ws, 4, ["步骤", "PDF页", "图册页", "倍数", "板件编号", "五金用量", "每套用量", "备注"])

    step_notes: dict[int, list[str]] = defaultdict(list)
    for issue in issues:
        if issue.get("step"):
            step_notes[issue["step"]].append(issue["title"])

    row = 5
    for step in sorted(model.steps, key=lambda s: s.no):
        hw_text = "、".join(
            f"{entry['code']} ×{entry['qty']}" for entry in sorted(step.hardware, key=lambda e: e["code"])
        ) or "—"
        per_unit = "、".join(
            f"{entry['code']} {entry['qty'] / step.repeat:g}" for entry in sorted(step.hardware, key=lambda e: e["code"])
        ) if step.repeat > 1 else "—"
        values = [
            step.no,
            step.page,
            step.page_label,
            f"×{step.repeat}" if step.repeat > 1 else "×1",
            "、".join(str(n) for n in step.panel_nos) or "—",
            hw_text,
            per_unit,
            "；".join(step_notes.get(step.no, [])) or "—",
        ]
        _body(ws, row, values)
        row += 1


def _build_issues_sheet(ws, issues) -> None:
    ws.sheet_view.showGridLines = False
    widths = [8, 20, 12, 14, 46, 40]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width
    _title(ws, "问题清单", 6)
    _subtitle(ws, "按严重程度排序。定位页码为 PDF 页码，括号内为图册页码。", 6, 2)
    _header(ws, 4, ["级别", "类型", "步骤", "PDF页", "问题说明", "修改建议"])

    row = 5
    for issue in issues:
        values = [
            _status_label(issue["severity"]),
            issue["title"],
            issue.get("step") or "—",
            issue.get("page") or "—",
            issue["detail"],
            issue["suggestion"],
        ]
        _body(ws, row, values)
        _paint_cell(ws, row, 1, issue["severity"])
        row += 1

    if not issues:
        ws.merge_cells(start_row=5, start_column=1, end_row=5, end_column=6)
        ws.cell(row=5, column=1, value="未发现需要修正的问题。").font = Font(size=11, bold=True, color=GREEN_TEXT)


def _build_notes_sheet(ws) -> None:
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 95
    _title(ws, "检查说明与使用边界", 2)
    rows = [
        ("五金用量合计", "逐项累加所有步骤用量表，与五金清单的需求数量比较。清单中的 x66+2 表示需求 66 件、另附 2 件备用；核对以 66 为准，备用单独展示。"),
        ("五金编号合法性", "步骤用量表中的字母必须存在于五金清单，防止字母错标或清单漏项。"),
        ("步骤用量表结构", "检查同一编号是否在一张表中重复、数量是否为 0、数量能否被步骤的 ×N 重复倍数整除。"),
        ("用量表与图形标注", "用量表列出的五金，图中应至少出现一次字母标注。部分五金以图形直接示意、不标字母时，此条仅作提示。"),
        ("板件编号合法性", "安装步骤与装配总览中的板件编号必须存在于板件清单。"),
        ("板件用量与重复模块", "按 ×N 重复的步骤，如果每套都使用某板件，清单数量必须不少于 N 且能被 N 整除，否则该编号很可能标错。"),
        ("板件清单覆盖", "清单中的板件应至少出现在一个安装步骤中；总览与步骤的编号应互相对应。"),
        ("同图多次标注", "同一板件在一张步骤图里出现多次，可能是爆炸图与装配图两个视图，也可能确实标错。工具将其列为“提示”而非硬错误，需人工确认。"),
        ("识别边界", "工具依赖 PDF 文字层。文字已转曲或扫描件无法自动识别，会给出提示并要求人工复核。"),
        ("模板边界", "当前版本针对 595×808 的柜体安装说明书模板（五金清单 + Parts List + 编号步骤卡）校准。版式变化较大时，需要调整识别参数。"),
        ("数据安全", "全部解析在本机完成，不上传图纸，不调用云端 OCR 或大模型。"),
    ]
    row = 3
    for label, text in rows:
        ws.cell(row=row, column=1, value=label).font = Font(bold=True, size=10, color=NAVY)
        ws.cell(row=row, column=1).alignment = Alignment(vertical="top", wrap_text=True)
        ws.cell(row=row, column=2, value=text).font = Font(size=10, color="1C2A3A")
        ws.cell(row=row, column=2).alignment = Alignment(vertical="top", wrap_text=True)
        ws.row_dimensions[row].height = 34
        row += 1
