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

PCS_QTY_RE = re.compile(r"^[×x]?\s*(\d+)(?:\s*\+\s*(\d+))?\s*PCS?$", re.IGNORECASE)
PCS_TAIL_RE = re.compile(r"(\d+)\s*PCS?$", re.IGNORECASE)
X_QTY_RE = re.compile(r"^[×x]\s*(\d+)$", re.IGNORECASE)
STEP_TABLE_RE = re.compile(r"^([A-Z]{1,2})\.\s*[×x](\d+)\s*PCS$", re.IGNORECASE)

TEMPLATE_NAMES = {
    "1145": "Aosom 1145 版式",
    "1235": "Aosom 1235 版式",
    "aosom24": "遨森 24 页版式",
}


def detect_template(pages: list[PageInfo]) -> str:
    text = " ".join(span.text for page in pages for span in page.spans).upper()
    if "LHNS174" in text or (
        "ASSEMBLY INSTRUCTION" in text
        and "STEP 1" in text
        and "EXPLOSION FIGURE" not in text
        and "ASSEMBLY OVERVIEW" not in text
    ):
        return "lingkewu"
    if "EXPLOSION FIGURE" in text:
        if len(pages) >= 24:
            return "aosom24"
        return "1235"
    if "ASSEMBLY OVERVIEW" in text:
        return "1145"
    return "1041"


def parse_aosom_pdf(
    path: str,
    display_name: str | None,
    pages: list[PageInfo],
    template: str,
    page_count: int,
) -> DocumentModel:
    if template == "1145":
        bom_page = _detect_bom_page_1145(pages)
        parts_page = _find_page(pages, "PART LIST")
        overview_page = _find_page(pages, "ASSEMBLY OVERVIEW")
        bom = _parse_bom_1145(bom_page)
        parts = _parse_parts_1145(parts_page)
        overview, overview_boxes = _parse_overview_1145(overview_page)
        steps, step_pages = _parse_steps_1145(pages, bom_page.index, parts)
    elif template in {"1235", "aosom24"}:
        bom_page = _detect_bom_page_1235(pages)
        parts_page = _detect_parts_page_1235(pages, bom_page)
        overview_page = _find_page(pages, "EXPLOSION FIGURE")
        bom = _parse_bom_1235(bom_page)
        parts = _parse_parts_1235(parts_page)
        overview, overview_boxes = _parse_overview_1235(overview_page)
        steps, step_pages = _parse_steps_1235(pages, parts_page.index, parts, template)
    else:
        raise ParseError("未知的 Aosom 模板版本。")

    if not steps:
        raise ParseError("未识别到任何安装步骤页，请确认 PDF 是否为该模板导出的安装说明书。")
    if not bom:
        raise ParseError("未识别到五金清单。")
    if not parts:
        raise ParseError("未识别到板件清单。")

    bom_page.role = "hardware"
    parts_page.role = "parts"
    overview_page.role = "overview"
    _mark_other_pages(pages)

    warnings = _collect_warnings(pages, steps, overview, template, max(parts))
    detected = {
        "template": template,
        "template_name": TEMPLATE_NAMES[template],
        "template_family": "aosom",
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


def _find_page(pages: list[PageInfo], phrase: str) -> PageInfo:
    wanted = phrase.upper()
    for page in pages:
        if any(wanted in span.text.upper() for span in page.spans):
            return page
    raise ParseError(f"未找到包含“{phrase}”的页面。")


def _detect_bom_page_1145(pages: list[PageInfo]) -> PageInfo:
    scored: list[tuple[int, PageInfo]] = []
    for page in pages[: min(10, len(pages))]:
        score = sum(
            1
            for span in page.spans
            if 11.0 <= span.size <= 14.0 and PCS_QTY_RE.fullmatch(span.text)
        )
        scored.append((score, page))
    score, page = max(scored, key=lambda item: item[0])
    if score < 4:
        raise ParseError("1145 五金清单识别失败：未找到成组的 PCS 数量。")
    return page


def _detect_bom_page_1235(pages: list[PageInfo]) -> PageInfo:
    scored: list[tuple[int, PageInfo]] = []
    for page in pages[: min(10, len(pages))]:
        code_headers = sum(
            1
            for span in page.spans
            if CODE_RE.fullmatch(span.text) and 16.0 <= span.size <= 18.0
        )
        x_qtys = sum(
            1
            for span in page.spans
            if 12.0 <= span.size <= 16.0 and X_QTY_RE.fullmatch(span.text)
        )
        scored.append((code_headers * 3 + x_qtys, page))
    score, page = max(scored, key=lambda item: item[0])
    if score < 10:
        raise ParseError("1235 五金清单识别失败：未找到 A-K 五金表。")
    return page


def _detect_parts_page_1235(pages: list[PageInfo], bom_page: PageInfo) -> PageInfo:
    scored: list[tuple[int, PageInfo]] = []
    for page in pages[: min(10, len(pages))]:
        if page.index == bom_page.index:
            continue
        score = sum(
            1
            for span in page.spans
            if 13.5 <= span.size <= 15.0 and X_QTY_RE.fullmatch(span.text)
        )
        scored.append((score, page))
    score, page = max(scored, key=lambda item: item[0])
    if score < 8:
        raise ParseError("1235 板件清单识别失败：未找到成组的 xN 数量。")
    return page


def _parse_bom_1145(page: PageInfo) -> dict[str, BomItem]:
    quantities = [
        span
        for span in page.spans
        if 11.0 <= span.size <= 14.0 and PCS_QTY_RE.fullmatch(span.text)
    ]
    ordered = _row_major(quantities)
    if len(ordered) < 3:
        raise ParseError("1145 五金清单数量过少。")
    items: dict[str, BomItem] = {}
    for index, qty_span in enumerate(ordered):
        code = chr(ord("A") + index)
        match = PCS_QTY_RE.fullmatch(qty_span.text)
        if match is None:
            continue
        box = _clip_box(
            page,
            [qty_span.x0 - 58, qty_span.y0 - 16, qty_span.x1 + 10, qty_span.y1 + 18],
        )
        items[code] = BomItem(
            code=code,
            qty=int(match.group(1)),
            spare=int(match.group(2) or 0),
            box=box,
            page=page.index,
        )
    if len(items) < 3:
        raise ParseError("1145 五金清单解析结果过少。")
    return items


def _parse_bom_1235(page: PageInfo) -> dict[str, BomItem]:
    quantities = [
        span
        for span in page.spans
        if 12.0 <= span.size <= 16.0 and X_QTY_RE.fullmatch(span.text)
    ]
    code_labels = _dedupe_code_labels(
        [
            span
            for span in page.spans
            if 15.8 <= span.size <= 18.0 and CODE_RE.fullmatch(span.text)
        ]
    )
    if len(quantities) < 3 or len(code_labels) < 3:
        raise ParseError("1235 五金清单数量过少。")

    spare_spans = [
        span
        for span in page.spans
        if NUMBER_RE.fullmatch(span.text) and 9.0 <= span.size <= 11.5
    ]
    used_spares: set[int] = set()
    used_quantities: set[int] = set()
    items: dict[str, BomItem] = {}
    qty_spans_by_code: dict[str, Span] = {}

    for code_span in _row_major(code_labels):
        candidates: list[tuple[float, int, Span]] = []
        for qty_index, qty_span in enumerate(quantities):
            if qty_index in used_quantities:
                continue
            gap = qty_span.x0 - code_span.x1
            dy = abs(qty_span.cy - code_span.cy)
            if not (0 <= gap <= 45 and dy <= 20):
                continue
            candidates.append((gap + dy * 3, qty_index, qty_span))
        if not candidates:
            continue
        _, qty_index, qty_span = min(candidates, key=lambda item: item[0])
        used_quantities.add(qty_index)
        qty_spans_by_code[code_span.text.upper()] = qty_span

    remaining_quantities = _row_major(
        [span for index, span in enumerate(quantities) if index not in used_quantities]
    )
    alphabet = [chr(ord("A") + index) for index in range(26)]
    missing_codes = [code for code in alphabet if code not in qty_spans_by_code]
    for code, qty_span in zip(missing_codes, remaining_quantities):
        qty_spans_by_code[code] = qty_span

    for code in sorted(qty_spans_by_code, key=lambda item: _code_sort_key(item)):
        qty_span = qty_spans_by_code[code]
        match = X_QTY_RE.fullmatch(qty_span.text)
        if match is None:
            continue
        spare = 0
        spare_candidates: list[tuple[float, int, Span]] = []
        for spare_index, spare_span in enumerate(spare_spans):
            if spare_index in used_spares:
                continue
            if not (qty_span.x0 - 55 <= spare_span.x0 <= qty_span.x1 - 2):
                continue
            if not (qty_span.y1 - 6 <= spare_span.y0 <= qty_span.y1 + 22):
                continue
            distance = abs(spare_span.x0 - (qty_span.x0 - 15)) + abs(
                spare_span.y0 - (qty_span.y1 - 1)
            )
            spare_candidates.append((distance, spare_index, spare_span))
        if spare_candidates:
            _, spare_index, spare_span = min(spare_candidates, key=lambda item: item[0])
            used_spares.add(spare_index)
            spare = int(spare_span.text)
        box = _clip_box(
            page,
            [qty_span.x0 - 34, qty_span.y0 - 8, qty_span.x1 + 12, qty_span.y1 + 24],
        )
        items[code] = BomItem(
            code=code,
            qty=int(match.group(1)),
            spare=spare,
            box=box,
            page=page.index,
        )
    if len(items) < 3:
        raise ParseError("1235 五金清单解析结果过少。")
    return items


def _parse_parts_1145(page: PageInfo) -> dict[int, PartItem]:
    quantity_spans = [
        span
        for span in page.spans
        if 15.0 <= span.size <= 18.0 and PCS_TAIL_RE.search(span.text)
    ]
    ordered = _row_major(quantity_spans)
    parts: dict[int, PartItem] = {}
    number_candidates = [
        span
        for span in page.spans
        if NUMBER_RE.fullmatch(span.text) and 14.0 <= span.size <= 18.0
    ]
    for index, qty_span in enumerate(ordered):
        match = PCS_TAIL_RE.search(qty_span.text)
        if match is None:
            continue
        no = _number_for_quantity(qty_span, number_candidates)
        if no is None:
            no = index + 1
        if no in parts:
            continue
        box = _clip_box(
            page,
            [qty_span.x0 - 10, qty_span.y0 - 20, qty_span.x1 + 24, qty_span.y1 + 185],
        )
        parts[no] = PartItem(no=no, qty=int(match.group(1)), box=box, page=page.index)
    if len(parts) < 3:
        raise ParseError("1145 板件清单解析结果过少。")
    return parts


def _parse_parts_1235(page: PageInfo) -> dict[int, PartItem]:
    quantity_spans = [
        span
        for span in page.spans
        if 13.5 <= span.size <= 15.0 and X_QTY_RE.fullmatch(span.text)
    ]
    ordered = _row_major(quantity_spans)
    if len(ordered) < 3:
        raise ParseError("1235 板件清单数量过少。")
    parts: dict[int, PartItem] = {}
    for index, qty_span in enumerate(ordered, start=1):
        match = X_QTY_RE.fullmatch(qty_span.text)
        if match is None:
            continue
        box = _clip_box(
            page,
            [qty_span.x0 - 66, qty_span.y0 - 24, qty_span.x1 + 32, qty_span.y1 + 80],
        )
        parts[index] = PartItem(no=index, qty=int(match.group(1)), box=box, page=page.index)
    if len(parts) < 3:
        raise ParseError("1235 板件清单解析结果过少。")
    return parts


def _parse_overview_1145(page: PageInfo) -> tuple[list[int], list[dict[str, Any]]]:
    numbers = [
        span
        for span in page.spans
        if NUMBER_RE.fullmatch(span.text)
        and 13.5 <= span.size <= 15.5
        and 90 <= span.y0 <= page.height - 100
        and 20 <= span.x0 <= page.width - 20
    ]
    return _overview_result(page, numbers)


def _parse_overview_1235(page: PageInfo) -> tuple[list[int], list[dict[str, Any]]]:
    numbers = [
        span
        for span in page.spans
        if NUMBER_RE.fullmatch(span.text)
        and 12.5 <= span.size <= 13.5
        and 90 <= span.y0 <= page.height - 100
        and 20 <= span.x0 <= page.width - 20
    ]
    return _overview_result(page, numbers)


def _overview_result(page: PageInfo, numbers: list[Span]) -> tuple[list[int], list[dict[str, Any]]]:
    seen: set[int] = set()
    boxes: list[dict[str, Any]] = []
    for span in numbers:
        no = int(span.text)
        seen.add(no)
        boxes.append({"no": no, "page": page.index, "box": span.box(7)})
    return sorted(seen), boxes


def _parse_steps_1145(
    pages: list[PageInfo], after_index: int, parts: dict[int, PartItem]
) -> tuple[list[Step], list[PageInfo]]:
    step_pages: list[PageInfo] = []
    steps: list[Step] = []
    for page in pages:
        if page.index <= after_index:
            continue
        labels = [
            span
            for span in page.spans
            if NUMBER_RE.fullmatch(span.text)
            and 33.0 <= span.size <= 38.0
            and span.x0 < 100
            and span.y0 < page.height - 30
        ]
        labels.sort(key=lambda span: span.y0)
        if not labels:
            continue
        page.role = "steps"
        page.step_nos = [int(span.text) for span in labels]
        step_pages.append(page)
        steps.extend(_parse_cards_1145(page, labels))
    return steps, step_pages


def _parse_cards_1145(page: PageInfo, labels: list[Span]) -> list[Step]:
    steps: list[Step] = []
    for index, label in enumerate(labels):
        top = max(6.0, label.y0 - 12)
        bottom = labels[index + 1].y0 - 12 if index + 1 < len(labels) else page.height - 28
        card = [span for span in page.spans if top <= span.cy < bottom]

        hardware: list[dict[str, Any]] = []
        table_spans: list[Span] = []
        for span in card:
            match = STEP_TABLE_RE.fullmatch(span.text)
            if match and 12.0 <= span.size <= 14.5:
                hardware.append(
                    {
                        "code": match.group(1).upper(),
                        "qty": int(match.group(2)),
                        "box": span.box(4),
                    }
                )
                table_spans.append(span)
        table_box = _union_box([span.box(5) for span in table_spans], pad=5) if table_spans else None

        panels: list[dict[str, Any]] = []
        for span in card:
            if not NUMBER_RE.fullmatch(span.text) or not (13.5 <= span.size <= 15.5):
                continue
            if table_box and _point_in_box(span.cx, span.cy, table_box):
                continue
            panels.append({"no": int(span.text), "box": span.box(3)})

        callout_counts: Counter[str] = Counter()
        for span in card:
            if not CODE_RE.fullmatch(span.text) or not (10.0 <= span.size <= 16.0):
                continue
            if table_box and _point_in_box(span.cx, span.cy, table_box):
                continue
            callout_counts[span.text] += 1

        steps.append(
            Step(
                no=int(label.text),
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


def _parse_steps_1235(
    pages: list[PageInfo],
    after_index: int,
    parts: dict[int, PartItem],
    template: str,
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
            if NUMBER_RE.fullmatch(span.text)
            and 16.5 <= span.size <= 18.5
            and span.x0 > page.width * 0.85
            and span.y0 < page.height - 30
        ]
        labels.sort(key=lambda span: span.y0)
        if not labels:
            continue
        page.role = "steps"
        page.step_nos = [int(span.text) for span in labels]
        step_pages.append(page)
        steps.extend(_parse_cards_1235(page, labels, max_part, template))
    return steps, step_pages


def _parse_cards_1235(
    page: PageInfo, labels: list[Span], max_part: int, template: str
) -> list[Step]:
    steps: list[Step] = []
    for index, label in enumerate(labels):
        top = max(6.0, label.y0 - 10)
        bottom = labels[index + 1].y0 - 10 if index + 1 < len(labels) else page.height - 28
        card = [span for span in page.spans if top <= span.cy < bottom]

        quantities = [
            span
            for span in card
            if 20.0 <= span.size <= 25.0 and X_QTY_RE.fullmatch(span.text)
        ]
        codes = [
            span
            for span in card
            if 13.5 <= span.size <= 16.5 and CODE_RE.fullmatch(span.text)
        ]
        hardware: list[dict[str, Any]] = []
        table_spans: list[Span] = []
        used_codes: set[int] = set()
        for qty_span in sorted(quantities, key=lambda span: (span.y0, span.x0)):
            match = X_QTY_RE.fullmatch(qty_span.text)
            if match is None:
                continue
            candidates: list[tuple[float, int, Span]] = []
            for code_index, code_span in enumerate(codes):
                if code_index in used_codes:
                    continue
                gap = qty_span.x0 - code_span.x1
                if not (35 <= gap <= 160):
                    continue
                if abs(code_span.cy - qty_span.cy) > 16:
                    continue
                score = gap + abs(code_span.cy - qty_span.cy) * 2
                candidates.append((score, code_index, code_span))
            if not candidates:
                continue
            _, code_index, code_span = min(candidates, key=lambda item: item[0])
            used_codes.add(code_index)
            box = _union_box([code_span.box(3), qty_span.box(3)])
            hardware.append(
                {
                    "code": code_span.text.upper(),
                    "qty": int(match.group(1)),
                    "box": box,
                }
            )
            table_spans.extend([code_span, qty_span])
        table_box = _union_box([span.box(4) for span in table_spans], pad=4) if table_spans else None

        panels: list[dict[str, Any]] = []
        for span in card:
            if not NUMBER_RE.fullmatch(span.text) or not (12.5 <= span.size <= 13.5):
                continue
            no = int(span.text)
            if max_part and no > max_part + 20:
                continue
            if table_box and _point_in_box(span.cx, span.cy, table_box):
                continue
            panels.append({"no": no, "box": span.box(3)})

        callout_counts: Counter[str] = Counter()
        max_callout_size = 17.5 if template == "aosom24" else 13.5
        for span in card:
            if not CODE_RE.fullmatch(span.text) or not (12.5 <= span.size <= max_callout_size):
                continue
            if table_box and _point_in_box(span.cx, span.cy, table_box):
                continue
            callout_counts[span.text] += 1

        steps.append(
            Step(
                no=int(label.text),
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


def _number_for_quantity(qty_span: Span, candidates: list[Span]) -> int | None:
    if qty_span.text.strip().startswith(tuple("0123456789")):
        prefix = re.match(r"^\s*(\d+)\s+", qty_span.text)
        if prefix:
            return int(prefix.group(1))
    options: list[tuple[float, int]] = []
    for span in candidates:
        if span is qty_span:
            continue
        gap = qty_span.x0 - span.x1
        if not (-2 <= gap <= 45):
            continue
        if abs(span.cy - qty_span.cy) > 7:
            continue
        options.append((gap + abs(span.cy - qty_span.cy) * 2, int(span.text)))
    if not options:
        return None
    return min(options, key=lambda item: item[0])[1]


def _dedupe_code_labels(spans: list[Span]) -> list[Span]:
    kept: list[Span] = []
    for span in _row_major(spans):
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(kept)
                if _box_overlap_ratio(existing, span) > 0.45
            ),
            None,
        )
        if duplicate_index is None:
            kept.append(span)
            continue
        if span.text < kept[duplicate_index].text:
            kept[duplicate_index] = span
    return kept


def _box_overlap_ratio(first: Span, second: Span) -> float:
    width = max(0.0, min(first.x1, second.x1) - max(first.x0, second.x0))
    height = max(0.0, min(first.y1, second.y1) - max(first.y0, second.y0))
    overlap = width * height
    if overlap <= 0:
        return 0.0
    smaller = min(
        max(1.0, (first.x1 - first.x0) * (first.y1 - first.y0)),
        max(1.0, (second.x1 - second.x0) * (second.y1 - second.y0)),
    )
    return overlap / smaller


def _code_sort_key(code: str) -> tuple[int, str]:
    return (len(code), code)


def _row_major(spans: list[Span], row_tolerance: float = 15.0) -> list[Span]:
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
        elif any(word in text for word in ("warranty", "care & maintenance", "wall mounting")):
            page.role = "appendix"
        else:
            page.role = "intro"


def _collect_warnings(
    pages: list[PageInfo],
    steps: list[Step],
    overview: list[int],
    template: str,
    max_part: int,
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
    if template in {"1235", "aosom24"}:
        warnings.append(
            f"{TEMPLATE_NAMES[template]}的板件清单编号为图形化数字，"
            f"已按清单网格顺序还原为 1–{max_part}。"
        )
    return warnings
