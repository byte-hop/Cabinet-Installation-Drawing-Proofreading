from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any

from .pdf_parser import (
    BomItem,
    DocumentModel,
    PageInfo,
    ParseError,
    PartItem,
    Span,
    Step,
    _point_in_box,
    _union_box,
)


NUMBER_RE = re.compile(r"^\d+$")
CODE_RE = re.compile(r"^[A-Z]{1,2}$")
STEP_RE = re.compile(r"^STEP\s+(\d+)$", re.IGNORECASE)
X_QTY_RE = re.compile(r"^[×x]\s*(\d+)$", re.IGNORECASE)
CALLOUT_RE = re.compile(r"^([A-Z]{1,2})(?:[-–]\d+|[xX]\d+)?$")

TEMPLATE_NAME = "领客屋版式"


def parse_lingkewu_pdf(
    path: str,
    display_name: str | None,
    pages: list[PageInfo],
    page_count: int,
) -> DocumentModel:
    parts_page = _detect_parts_page(pages)
    bom_page = _detect_bom_page(pages, parts_page)
    bom = _parse_bom(bom_page)
    parts = _parse_parts(parts_page)
    overview_page = _detect_overview_page(pages, parts_page, bom_page, max(parts))
    overview, overview_boxes = _parse_overview(overview_page, max(parts))
    steps, step_pages = _parse_steps(pages, overview_page.index, bom, parts)

    if not steps:
        raise ParseError("未识别到任何安装步骤页，请确认 PDF 是否为领客屋版式安装说明书。")

    bom_page.role = "hardware"
    parts_page.role = "parts"
    overview_page.role = "overview"
    _mark_other_pages(pages)

    warnings = _collect_warnings(pages, steps, overview)
    detected = {
        "template": "lingkewu",
        "template_name": TEMPLATE_NAME,
        "template_family": "lingkewu",
        "bom_page": bom_page.index,
        "bom_page_label": bom_page.label,
        "parts_page": parts_page.index,
        "parts_page_label": parts_page.label,
        "overview_page": overview_page.index,
        "overview_page_label": overview_page.label,
        "step_page_count": len(step_pages),
        "step_min": min(step.no for step in steps),
        "step_max": max(step.no for step in steps),
        "table_count": sum(1 for step in steps if step.hardware),
    }
    return DocumentModel(
        file_name=display_name or os.path.basename(path),
        file_size=os.path.getsize(path),
        page_count=page_count,
        pages=pages,
        bom=bom,
        parts=parts,
        overview=overview,
        overview_boxes=overview_boxes,
        steps=steps,
        detected=detected,
        parse_warnings=warnings,
    )


def _detect_parts_page(pages: list[PageInfo]) -> PageInfo:
    scored: list[tuple[int, PageInfo]] = []
    for page in pages[: min(10, len(pages))]:
        numbers = sum(
            1
            for span in page.spans
            if NUMBER_RE.fullmatch(span.text) and 17.2 <= span.size <= 18.8
        )
        quantities = sum(
            1
            for span in page.spans
            if 18.2 <= span.size <= 19.8 and X_QTY_RE.fullmatch(span.text)
        )
        scored.append((numbers + quantities, page))
    score, page = max(scored, key=lambda item: item[0])
    if score < 12:
        raise ParseError("领客屋板件清单识别失败：未找到成组的板件号与 xN 数量。")
    return page


def _detect_bom_page(pages: list[PageInfo], parts_page: PageInfo) -> PageInfo:
    scored: list[tuple[int, PageInfo]] = []
    for page in pages[: min(12, len(pages))]:
        if page.index == parts_page.index:
            continue
        codes = sum(
            1
            for span in page.spans
            if CODE_RE.fullmatch(span.text) and 14.8 <= span.size <= 18.8
        )
        quantities = sum(
            1
            for span in page.spans
            if 18.2 <= span.size <= 19.8 and X_QTY_RE.fullmatch(span.text)
        )
        scored.append((codes + quantities, page))
    score, page = max(scored, key=lambda item: item[0])
    if score < 12:
        raise ParseError("领客屋五金清单识别失败：未找到 A–DR 编号与 xN 数量。")
    return page


def _detect_overview_page(
    pages: list[PageInfo], parts_page: PageInfo, bom_page: PageInfo, max_part: int
) -> PageInfo:
    scored: list[tuple[int, PageInfo]] = []
    for page in pages:
        if page.index <= max(parts_page.index, bom_page.index):
            continue
        if any(STEP_RE.fullmatch(span.text) for span in page.spans):
            continue
        score = sum(
            1
            for span in page.spans
            if NUMBER_RE.fullmatch(span.text)
            and 16.0 <= span.size <= 16.8
            and 1 <= int(span.text) <= max_part
        )
        scored.append((score, page))
    if not scored:
        raise ParseError("领客屋装配总览页识别失败。")
    score, page = max(scored, key=lambda item: item[0])
    if score < 8:
        raise ParseError("领客屋装配总览页识别失败：未找到成组的板件编号。")
    return page


def _parse_bom(page: PageInfo) -> dict[str, BomItem]:
    codes = _unique_codes(
        [
            span
            for span in page.spans
            if CODE_RE.fullmatch(span.text) and 14.8 <= span.size <= 18.8
        ]
    )
    quantities = [
        span
        for span in page.spans
        if 18.2 <= span.size <= 19.8 and X_QTY_RE.fullmatch(span.text)
    ]
    if len(codes) < 4 or len(quantities) < 4:
        raise ParseError("领客屋五金清单内容过少。")

    used: set[int] = set()
    items: dict[str, BomItem] = {}
    for code_span in sorted(codes, key=lambda span: (span.y0, span.x0)):
        candidates: list[tuple[float, int, Span]] = []
        for index, qty_span in enumerate(quantities):
            if index in used:
                continue
            gap = qty_span.x0 - code_span.x1
            dy = abs(qty_span.cy - code_span.cy)
            if not (20 <= gap <= 130 and dy <= 22):
                continue
            candidates.append((gap + dy * 3, index, qty_span))
        if not candidates:
            continue
        _, index, qty_span = min(candidates, key=lambda item: item[0])
        used.add(index)
        match = X_QTY_RE.fullmatch(qty_span.text)
        if match is None:
            continue
        items[code_span.text] = BomItem(
            code=code_span.text,
            qty=int(match.group(1)),
            spare=0,
            box=_clip_box(
                page,
                [
                    code_span.x0 - 14,
                    code_span.y0 - 18,
                    qty_span.x1 + 14,
                    qty_span.y1 + 28,
                ],
            ),
            page=page.index,
        )
    if len(items) < 4:
        raise ParseError("领客屋五金清单解析结果过少。")
    return items


def _parse_parts(page: PageInfo) -> dict[int, PartItem]:
    number_spans = [
        span
        for span in page.spans
        if NUMBER_RE.fullmatch(span.text) and 17.2 <= span.size <= 18.8
    ]
    quantity_spans = [
        span
        for span in page.spans
        if 18.2 <= span.size <= 19.8 and X_QTY_RE.fullmatch(span.text)
    ]
    numbers = _row_major(number_spans)
    quantities = _row_major(quantity_spans)
    if len(numbers) < 4 or len(numbers) != len(quantities):
        raise ParseError("领客屋板件清单编号与数量数量不一致。")

    parts: dict[int, PartItem] = {}
    for number_span, qty_span in zip(numbers, quantities):
        match = X_QTY_RE.fullmatch(qty_span.text)
        if match is None:
            continue
        no = int(number_span.text)
        parts[no] = PartItem(
            no=no,
            qty=int(match.group(1)),
            box=_clip_box(
                page,
                [
                    number_span.x0 - 12,
                    number_span.y0 - 14,
                    qty_span.x1 + 14,
                    qty_span.y1 + 24,
                ],
            ),
            page=page.index,
        )
    if len(parts) < 4:
        raise ParseError("领客屋板件清单解析结果过少。")
    return parts


def _parse_overview(page: PageInfo, max_part: int) -> tuple[list[int], list[dict[str, Any]]]:
    seen: set[int] = set()
    boxes: list[dict[str, Any]] = []
    for span in page.spans:
        if not NUMBER_RE.fullmatch(span.text) or not (16.0 <= span.size <= 16.8):
            continue
        no = int(span.text)
        if not (1 <= no <= max_part):
            continue
        seen.add(no)
        boxes.append({"no": no, "page": page.index, "box": span.box(7)})
    return sorted(seen), boxes


def _parse_steps(
    pages: list[PageInfo],
    after_index: int,
    bom: dict[str, BomItem],
    parts: dict[int, PartItem],
) -> tuple[list[Step], list[PageInfo]]:
    step_pages: list[PageInfo] = []
    steps: list[Step] = []
    max_part = max(parts) if parts else 0
    for page in pages:
        if page.index <= after_index:
            continue
        labels = [
            span
            for span in page.spans
            if STEP_RE.fullmatch(span.text) and 19.5 <= span.size <= 21.5
        ]
        labels.sort(key=lambda span: span.y0)
        if not labels:
            continue
        page.role = "steps"
        page.step_nos = [int(STEP_RE.fullmatch(span.text).group(1)) for span in labels]
        step_pages.append(page)
        steps.extend(_parse_cards(page, labels, bom, max_part))
    return steps, step_pages


def _parse_cards(
    page: PageInfo,
    labels: list[Span],
    bom: dict[str, BomItem],
    max_part: int,
) -> list[Step]:
    steps: list[Step] = []
    for index, label in enumerate(labels):
        top = max(6.0, label.y0 - 12)
        bottom = labels[index + 1].y0 - 10 if index + 1 < len(labels) else page.height - 30
        card = [span for span in page.spans if top <= span.cy < bottom]

        quantities = [
            span
            for span in card
            if 8.8 <= span.size <= 10.3 and X_QTY_RE.fullmatch(span.text)
        ]
        table_codes = [
            span
            for span in card
            if 8.8 <= span.size <= 10.3 and CODE_RE.fullmatch(span.text)
        ]
        hardware: list[dict[str, Any]] = []
        table_spans: list[Span] = []
        used_codes: set[int] = set()
        for qty_span in sorted(quantities, key=lambda span: (span.y0, span.x0)):
            match = X_QTY_RE.fullmatch(qty_span.text)
            if match is None:
                continue
            candidates: list[tuple[float, int, Span]] = []
            for code_index, code_span in enumerate(table_codes):
                if code_index in used_codes:
                    continue
                gap = qty_span.x0 - code_span.x1
                dy = abs(code_span.cy - qty_span.cy)
                if not (18 <= gap <= 90 and dy <= 7):
                    continue
                candidates.append((gap + dy * 3, code_index, code_span))
            if not candidates:
                continue
            _, code_index, code_span = min(candidates, key=lambda item: item[0])
            used_codes.add(code_index)
            hardware.append(
                {
                    "code": code_span.text,
                    "qty": int(match.group(1)),
                    "box": _union_box([code_span.box(3), qty_span.box(3)]),
                }
            )
            table_spans.extend([code_span, qty_span])

        table_box = (
            _union_box([span.box(4) for span in table_spans], pad=4)
            if table_spans
            else None
        )
        panels: list[dict[str, Any]] = []
        for span in card:
            if not NUMBER_RE.fullmatch(span.text) or not (15.8 <= span.size <= 17.0):
                continue
            no = int(span.text)
            if not (1 <= no <= max_part):
                continue
            if table_box and _point_in_box(span.cx, span.cy, table_box):
                continue
            panels.append({"no": no, "box": span.box(3)})

        callout_counts: Counter[str] = Counter()
        for span in card:
            if not (15.8 <= span.size <= 17.5):
                continue
            match = CALLOUT_RE.fullmatch(span.text)
            if match is None:
                continue
            code = match.group(1)
            if code not in bom:
                continue
            if table_box and _point_in_box(span.cx, span.cy, table_box):
                continue
            callout_counts[code] += 1

        step_no = int(STEP_RE.fullmatch(label.text).group(1))
        steps.append(
            Step(
                no=step_no,
                page=page.index,
                page_label=page.label,
                repeat=1,
                panels=sorted(panels, key=lambda item: (item["box"][1], item["box"][0])),
                hardware=hardware,
                table_box=table_box,
                callout_counts=dict(callout_counts),
            )
        )
    return steps


def _unique_codes(spans: list[Span]) -> list[Span]:
    result: list[Span] = []
    for span in sorted(spans, key=lambda item: (round(item.y0, 1), item.x0)):
        if any(
            existing.text == span.text
            and abs(existing.cx - span.cx) < 4
            and abs(existing.cy - span.cy) < 4
            for existing in result
        ):
            continue
        result.append(span)
    return result


def _row_major(spans: list[Span], row_tolerance: float = 18.0) -> list[Span]:
    ordered = sorted(spans, key=lambda span: (span.y0, span.x0))
    rows: list[list[Span]] = []
    for span in ordered:
        if not rows or abs(span.y0 - rows[-1][-1].y0) > row_tolerance:
            rows.append([span])
        else:
            rows[-1].append(span)
    result: list[Span] = []
    for row in rows:
        result.extend(sorted(row, key=lambda span: span.x0))
    return result


def _clip_box(page: PageInfo, box: list[float]) -> list[float]:
    return [
        round(max(5.0, min(page.width - 5.0, box[0])), 2),
        round(max(5.0, min(page.height - 5.0, box[1])), 2),
        round(max(5.0, min(page.width - 5.0, box[2])), 2),
        round(max(5.0, min(page.height - 5.0, box[3])), 2),
    ]


def _mark_other_pages(pages: list[PageInfo]) -> None:
    for page in pages:
        if page.role != "other":
            continue
        text = " ".join(span.text for span in page.spans).lower()
        if page.index == 1:
            page.role = "cover"
        elif any(word in text for word in ("warranty", "care", "maintenance")):
            page.role = "appendix"
        else:
            page.role = "intro"


def _collect_warnings(
    pages: list[PageInfo], steps: list[Step], overview: list[int]
) -> list[str]:
    warnings: list[str] = []
    if not overview:
        warnings.append("装配总览页未识别到板件编号，已跳过总览交叉检查。")
    for page in pages:
        if page.role == "steps" and len(page.spans) < 3:
            warnings.append(f"第 {page.index} 页是步骤页，但没有可提取的文字层，可能已转曲。")
    no_table = sorted({step.no for step in steps if not step.hardware})
    if no_table:
        warnings.append(
            "以下步骤没有五金用量表（通常为纯拼装或检查步骤）："
            + "、".join(str(no) for no in no_table)
        )
    return warnings
