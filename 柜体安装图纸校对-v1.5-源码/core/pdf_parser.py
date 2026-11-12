from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import pymupdf


NUMBER_RE = re.compile(r"^\d+$")
CODE_RE = re.compile(r"^[A-Z]{1,2}$")
QTY_RE = re.compile(r"^x(\d+)(?:\+(\d+))?$")
PAGE_LABEL_RE = re.compile(r"^(\d+)\s*/\s*(\d+)$")


class ParseError(RuntimeError):
    pass


@dataclass
class Span:
    page: int
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    font: str

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    def box(self, pad: float = 0.0) -> list[float]:
        return [
            round(self.x0 - pad, 2),
            round(self.y0 - pad, 2),
            round(self.x1 + pad, 2),
            round(self.y1 + pad, 2),
        ]


@dataclass
class PageInfo:
    index: int
    width: float
    height: float
    spans: list[Span]
    label: str = ""
    role: str = "other"
    step_nos: list[int] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "width": round(self.width, 2),
            "height": round(self.height, 2),
            "label": self.label,
            "role": self.role,
            "steps": self.step_nos,
        }


@dataclass
class BomItem:
    code: str
    qty: int
    spare: int
    box: list[float]
    page: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "qty": self.qty,
            "spare": self.spare,
            "box": self.box,
            "page": self.page,
        }


@dataclass
class PartItem:
    no: int
    qty: int
    box: list[float]
    page: int

    def to_payload(self) -> dict[str, Any]:
        return {"no": self.no, "qty": self.qty, "box": self.box, "page": self.page}


@dataclass
class Step:
    no: int
    page: int
    page_label: str
    repeat: int
    panels: list[dict[str, Any]]
    hardware: list[dict[str, Any]]
    table_box: list[float] | None
    callout_counts: dict[str, int]

    @property
    def panel_nos(self) -> list[int]:
        return sorted({int(p["no"]) for p in self.panels})

    def to_payload(self) -> dict[str, Any]:
        return {
            "no": self.no,
            "page": self.page,
            "page_label": self.page_label,
            "repeat": self.repeat,
            "panels": self.panels,
            "panel_nos": self.panel_nos,
            "hardware": self.hardware,
            "table_box": self.table_box,
            "callout_counts": self.callout_counts,
        }


@dataclass
class DocumentModel:
    file_name: str
    file_size: int
    page_count: int
    pages: list[PageInfo]
    bom: dict[str, BomItem]
    parts: dict[int, PartItem]
    overview: list[int]
    overview_boxes: list[dict[str, Any]]
    steps: list[Step]
    detected: dict[str, Any]
    parse_warnings: list[str]

    def to_payload(self) -> dict[str, Any]:
        return {
            "file": {
                "name": self.file_name,
                "size": self.file_size,
                "pages": self.page_count,
            },
            "detected": self.detected,
            "pages": [p.to_payload() for p in self.pages],
            "bom": [item.to_payload() for item in sorted(self.bom.values(), key=_bom_sort_key)],
            "parts": [item.to_payload() for item in sorted(self.parts.values(), key=lambda p: p.no)],
            "overview": self.overview,
            "overview_boxes": self.overview_boxes,
            "steps": [step.to_payload() for step in sorted(self.steps, key=lambda s: s.no)],
            "parse_warnings": self.parse_warnings,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "DocumentModel":
        file_info = payload.get("file") or {}
        pages = [
            PageInfo(
                index=int(page.get("index") or 0),
                width=float(page.get("width") or 0),
                height=float(page.get("height") or 0),
                spans=[],
                label=str(page.get("label") or ""),
                role=str(page.get("role") or "other"),
                step_nos=[int(value) for value in page.get("steps") or []],
            )
            for page in payload.get("pages") or []
        ]
        bom = {
            str(item.get("code")): BomItem(
                code=str(item.get("code")),
                qty=int(item.get("qty") or 0),
                spare=int(item.get("spare") or 0),
                box=[float(value) for value in item.get("box") or []],
                page=int(item.get("page") or 0),
            )
            for item in payload.get("bom") or []
        }
        parts = {
            int(item.get("no") or 0): PartItem(
                no=int(item.get("no") or 0),
                qty=int(item.get("qty") or 0),
                box=[float(value) for value in item.get("box") or []],
                page=int(item.get("page") or 0),
            )
            for item in payload.get("parts") or []
        }
        steps = [
            Step(
                no=int(item.get("no") or 0),
                page=int(item.get("page") or 0),
                page_label=str(item.get("page_label") or ""),
                repeat=int(item.get("repeat") or 1),
                panels=[dict(panel) for panel in item.get("panels") or []],
                hardware=[dict(entry) for entry in item.get("hardware") or []],
                table_box=[float(value) for value in item["table_box"]]
                if item.get("table_box")
                else None,
                callout_counts={
                    str(key): int(value) for key, value in (item.get("callout_counts") or {}).items()
                },
            )
            for item in payload.get("steps") or []
        ]
        return cls(
            file_name=str(file_info.get("name") or ""),
            file_size=int(file_info.get("size") or 0),
            page_count=int(file_info.get("pages") or len(pages)),
            pages=pages,
            bom=bom,
            parts=parts,
            overview=[int(value) for value in payload.get("overview") or []],
            overview_boxes=[dict(item) for item in payload.get("overview_boxes") or []],
            steps=steps,
            detected=dict(payload.get("detected") or {}),
            parse_warnings=[str(value) for value in payload.get("parse_warnings") or []],
        )


def _bom_sort_key(item: BomItem) -> tuple[int, str]:
    order = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if len(item.code) == 1:
        return (order.index(item.code), item.code)
    return (100, item.code)


def parse_pdf(path: str, display_name: str | None = None) -> DocumentModel:
    doc = pymupdf.open(path)
    try:
        pages = [_extract_page(doc, i) for i in range(doc.page_count)]
        from .aosom_parser import detect_template, parse_aosom_pdf
        from .lingkewu_parser import parse_lingkewu_pdf

        template = detect_template(pages)
        if template == "lingkewu":
            return parse_lingkewu_pdf(path, display_name, pages, doc.page_count)
        if template in {"1145", "1235", "aosom24"}:
            return parse_aosom_pdf(path, display_name, pages, template, doc.page_count)

        parts_page = _detect_parts_page(pages)
        bom_page = _detect_bom_page(pages, parts_page)

        bom = _parse_bom(bom_page)
        parts = _parse_parts(parts_page)
        overview, overview_boxes = _parse_overview(bom_page)

        step_pages = [p for p in pages if p.index > parts_page.index and _step_labels(p)]
        steps: list[Step] = []
        for page in step_pages:
            labels = _step_labels(page)
            page.step_nos = [int(s.text) for s in labels]
            page.role = "steps"
            steps.extend(_parse_step_page(page, labels))

        if not steps:
            raise ParseError("未识别到任何安装步骤页，请确认 PDF 是否为该模板导出的安装说明书。")

        bom_page.role = "hardware"
        parts_page.role = "parts"
        _mark_decorative_pages(pages)

        warnings = _collect_parse_warnings(pages, steps, overview)
        detected = {
            "template": "1041",
            "template_name": "英驰版式",
            "template_family": "legacy",
            "bom_page": bom_page.index,
            "bom_page_label": bom_page.label,
            "parts_page": parts_page.index,
            "parts_page_label": parts_page.label,
            "step_page_count": len(step_pages),
            "step_min": min(s.no for s in steps),
            "step_max": max(s.no for s in steps),
            "table_count": sum(1 for s in steps if s.hardware),
        }

        return DocumentModel(
            file_name=display_name or os.path.basename(path),
            file_size=os.path.getsize(path),
            page_count=doc.page_count,
            pages=pages,
            bom=bom,
            parts=parts,
            overview=overview,
            overview_boxes=overview_boxes,
            steps=steps,
            detected=detected,
            parse_warnings=warnings,
        )
    finally:
        doc.close()


def _extract_page(doc: pymupdf.Document, index: int) -> PageInfo:
    page = doc[index]
    spans: list[Span] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for raw in line.get("spans", []):
                text = raw.get("text", "").strip()
                if not text:
                    continue
                x0, y0, x1, y1 = raw["bbox"]
                spans.append(
                    Span(
                        page=index + 1,
                        text=text,
                        x0=x0,
                        y0=y0,
                        x1=x1,
                        y1=y1,
                        size=float(raw.get("size", 0)),
                        font=str(raw.get("font", "")),
                    )
                )
    label = ""
    for span in spans:
        match = PAGE_LABEL_RE.fullmatch(span.text)
        if match and span.y0 > page.rect.height * 0.85:
            label = span.text
            break
    if not label:
        for span in spans:
            if (
                NUMBER_RE.fullmatch(span.text)
                and 10.5 <= span.size <= 13.5
                and span.y0 > page.rect.height * 0.94
            ):
                label = span.text
                break
    return PageInfo(
        index=index + 1,
        width=float(page.rect.width),
        height=float(page.rect.height),
        spans=spans,
        label=label,
    )


def _find_qty_right(
    anchor: Span,
    spans: list[Span],
    *,
    dy: float = 7,
    min_gap: float = 6,
    max_gap: float = 90,
    min_size: float = 0,
    max_size: float = 99,
) -> Span | None:
    candidates: list[tuple[float, Span]] = []
    for span in spans:
        if not QTY_RE.fullmatch(span.text):
            continue
        if not (min_size <= span.size <= max_size):
            continue
        gap = span.x0 - anchor.x1
        if gap < min_gap or gap > max_gap:
            continue
        if abs(span.cy - anchor.cy) > dy:
            continue
        candidates.append((gap + abs(span.cy - anchor.cy) * 2, span))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _find_qty_below(
    anchor: Span,
    spans: list[Span],
    *,
    dx_min: float = 100,
    dx_max: float = 200,
    dy_min: float = 35,
    dy_max: float = 140,
    min_size: float = 10,
    max_size: float = 16,
) -> Span | None:
    candidates: list[tuple[float, Span]] = []
    for span in spans:
        if not QTY_RE.fullmatch(span.text):
            continue
        if not (min_size <= span.size <= max_size):
            continue
        dx = span.x0 - anchor.x0
        dy = span.y0 - anchor.y1
        if not (dx_min <= dx <= dx_max and dy_min <= dy <= dy_max):
            continue
        candidates.append((abs(dx - 145) + abs(dy - 75), span))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _detect_bom_page(pages: list[PageInfo], parts_page: PageInfo) -> PageInfo:
    scores: list[tuple[int, PageInfo]] = []
    for page in pages[: min(8, len(pages))]:
        if page.index == parts_page.index:
            continue
        score = 0
        for span in page.spans:
            if not CODE_RE.fullmatch(span.text) or not (5.0 <= span.size <= 12.0):
                continue
            if _find_qty_right(span, page.spans, dy=9, min_gap=8, max_gap=95, min_size=6.5, max_size=9.5):
                score += 1
        scores.append((score, page))
    if not scores:
        raise ParseError("未找到五金清单页。")
    score, page = max(scores, key=lambda item: item[0])
    if score < 4:
        raise ParseError("五金清单页识别失败：清单中的编号与数量结构不足。")
    return page


def _detect_parts_page(pages: list[PageInfo]) -> PageInfo:
    for page in pages:
        if any(span.text.strip().lower() == "parts list" for span in page.spans):
            return page
    scored: list[tuple[int, PageInfo]] = []
    for page in pages[: min(10, len(pages))]:
        score = 0
        for span in page.spans:
            if not NUMBER_RE.fullmatch(span.text) or not (12.0 <= span.size <= 15.0):
                continue
            qty = _find_qty_below(span, page.spans, min_size=10, max_size=16)
            if qty:
                score += 1
        scored.append((score, page))
    if not scored:
        raise ParseError("未找到板件清单页。")
    score, page = max(scored, key=lambda item: item[0])
    if score < 4:
        raise ParseError("板件清单页识别失败：未找到成组的板件号与数量。")
    return page


def _parse_bom(page: PageInfo) -> dict[str, BomItem]:
    items: dict[str, BomItem] = {}
    letters = [
        span
        for span in page.spans
        if CODE_RE.fullmatch(span.text) and 5.0 <= span.size <= 12.0
    ]
    for span in sorted(letters, key=lambda s: (round(s.y0, 1), s.x0)):
        if span.text in items:
            continue
        qty_span = _find_qty_right(
            span, page.spans, dy=9, min_gap=8, max_gap=95, min_size=6.5, max_size=9.5
        )
        if qty_span is None:
            continue
        match = QTY_RE.fullmatch(qty_span.text)
        if match is None:
            continue
        box = [
            max(6.0, span.x0 - 12),
            max(6.0, span.y0 - 10),
            min(page.width - 6.0, span.x0 + 66),
            min(page.height - 6.0, span.y0 + 88),
        ]
        items[span.text] = BomItem(
            code=span.text,
            qty=int(match.group(1)),
            spare=int(match.group(2) or 0),
            box=[round(v, 2) for v in box],
            page=page.index,
        )
    if len(items) < 3:
        raise ParseError("五金清单解析结果过少，请检查清单页版式。")
    return items


def _parse_parts(page: PageInfo) -> dict[int, PartItem]:
    parts: dict[int, PartItem] = {}
    numbers = [
        span
        for span in page.spans
        if NUMBER_RE.fullmatch(span.text)
        and 12.0 <= span.size <= 14.5
        and span.y0 > 45
        and span.y0 < page.height - 55
    ]
    for span in sorted(numbers, key=lambda s: (round(s.y0, 1), s.x0)):
        no = int(span.text)
        if no in parts:
            continue
        qty_span = _find_qty_below(span, page.spans, min_size=10, max_size=16)
        if qty_span is None:
            continue
        match = QTY_RE.fullmatch(qty_span.text)
        if match is None:
            continue
        box = [
            max(6.0, span.x0 - 10),
            max(6.0, span.y0 - 14),
            min(page.width - 6.0, span.x0 + 176),
            min(page.height - 6.0, span.y0 + 116),
        ]
        parts[no] = PartItem(
            no=no,
            qty=int(match.group(1)),
            box=[round(v, 2) for v in box],
            page=page.index,
        )
    if len(parts) < 3:
        raise ParseError("板件清单解析结果过少，请检查清单页版式。")
    return parts


def _parse_overview(page: PageInfo) -> tuple[list[int], list[dict[str, Any]]]:
    numbers = [
        span
        for span in page.spans
        if NUMBER_RE.fullmatch(span.text)
        and 12.2 <= span.size <= 13.8
        and 55 < span.y0 < page.height - 90
    ]
    seen: set[int] = set()
    boxes: list[dict[str, Any]] = []
    for span in numbers:
        no = int(span.text)
        boxes.append({"no": no, "page": page.index, "box": span.box(7)})
        seen.add(no)
    return sorted(seen), boxes


def _step_labels(page: PageInfo) -> list[Span]:
    labels = [
        span
        for span in page.spans
        if NUMBER_RE.fullmatch(span.text)
        and span.x0 < 70
        and 13.8 <= span.size <= 15.3
        and 6 < span.y0 < page.height - 25
    ]
    labels.sort(key=lambda s: (s.y0, s.x0))
    deduped: list[Span] = []
    for span in labels:
        if deduped and abs(span.cy - deduped[-1].cy) < 8:
            continue
        deduped.append(span)
    return deduped


def _parse_step_page(page: PageInfo, labels: list[Span]) -> list[Step]:
    steps: list[Step] = []
    for index, label in enumerate(labels):
        top = max(6.0, label.y0 - 11)
        bottom = labels[index + 1].y0 - 11 if index + 1 < len(labels) else page.height - 25
        card = [span for span in page.spans if top <= span.cy < bottom]

        repeats = []
        for span in card:
            match = QTY_RE.fullmatch(span.text)
            if match and span.size > 30:
                repeats.append(int(match.group(1)))
        repeat = max(repeats) if repeats else 1

        panels = [
            {"no": int(span.text), "box": span.box(3)}
            for span in card
            if NUMBER_RE.fullmatch(span.text) and 12.2 <= span.size <= 13.7
        ]

        hardware, table_box = _parse_step_table(card)
        callout_counts = _count_callouts(card, table_box)

        steps.append(
            Step(
                no=int(label.text),
                page=page.index,
                page_label=page.label,
                repeat=repeat,
                panels=sorted(panels, key=lambda p: (p["box"][1], p["box"][0])),
                hardware=hardware,
                table_box=table_box,
                callout_counts=callout_counts,
            )
        )
    return steps


def _parse_step_table(card: list[Span]) -> tuple[list[dict[str, Any]], list[float] | None]:
    letters = [
        span
        for span in card
        if CODE_RE.fullmatch(span.text) and 6.0 <= span.size <= 9.2
    ]
    qtys = [
        span
        for span in card
        if QTY_RE.fullmatch(span.text) and 9.0 <= span.size <= 10.7
    ]
    entries: list[dict[str, Any]] = []
    used_letters: set[int] = set()
    for qty_span in sorted(qtys, key=lambda s: (round(s.cy, 1), s.x0)):
        candidates: list[tuple[float, int, Span]] = []
        for idx, letter in enumerate(letters):
            if idx in used_letters:
                continue
            gap = qty_span.x0 - letter.x1
            if not (8 <= gap <= 90):
                continue
            if abs(letter.cy - qty_span.cy) > 7:
                continue
            candidates.append((gap + abs(letter.cy - qty_span.cy) * 2, idx, letter))
        if not candidates:
            continue
        _, idx, letter = min(candidates, key=lambda item: item[0])
        used_letters.add(idx)
        match = QTY_RE.fullmatch(qty_span.text)
        if match is None:
            continue
        entries.append(
            {
                "code": letter.text,
                "qty": int(match.group(1)),
                "box": _union_box([letter.box(2), qty_span.box(2)]),
            }
        )

    if entries:
        table_box = _union_box([entry["box"] for entry in entries], pad=7)
    else:
        table_box = None
    return entries, table_box


def _count_callouts(card: list[Span], table_box: list[float] | None) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for span in card:
        if not CODE_RE.fullmatch(span.text):
            continue
        if not (6.0 <= span.size <= 24.0):
            continue
        if table_box and _point_in_box(span.cx, span.cy, table_box):
            continue
        counts[span.text] += 1
    return dict(counts)


def _union_box(boxes: list[list[float]], pad: float = 0.0) -> list[float]:
    xs0 = [b[0] for b in boxes]
    ys0 = [b[1] for b in boxes]
    xs1 = [b[2] for b in boxes]
    ys1 = [b[3] for b in boxes]
    return [
        round(min(xs0) - pad, 2),
        round(min(ys0) - pad, 2),
        round(max(xs1) + pad, 2),
        round(max(ys1) + pad, 2),
    ]


def _point_in_box(x: float, y: float, box: list[float]) -> bool:
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _mark_decorative_pages(pages: list[PageInfo]) -> None:
    for page in pages:
        if page.role in {"hardware", "parts", "steps"}:
            continue
        text = " ".join(span.text for span in page.spans).lower()
        if "tools required" in text:
            page.role = "cover"
        elif "wall mounting" in text or "care & maintenance" in text:
            page.role = "appendix"
        elif page.index <= 3:
            page.role = "intro"
        else:
            page.role = "other"


def _collect_parse_warnings(
    pages: list[PageInfo], steps: list[Step], overview: list[int]
) -> list[str]:
    warnings: list[str] = []
    if not overview:
        warnings.append("装配总览页未识别到板件编号，已跳过总览交叉检查。")
    for page in pages:
        if page.role == "steps" and len(page.spans) < 3:
            warnings.append(f"第 {page.index} 页是步骤页，但没有可提取的文字层，可能已转曲。")
    no_table = sorted({s.no for s in steps if not s.hardware})
    if no_table:
        warnings.append(
            "以下步骤没有五金用量表（通常为纯拼装或检查步骤）："
            + "、".join(str(n) for n in no_table)
        )
    return warnings


def page_image(path: str, page_no: int, dpi: int = 150) -> bytes:
    doc = pymupdf.open(path)
    try:
        page = doc[page_no - 1]
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        return pix.tobytes("png")
    finally:
        doc.close()


def crop_image(path: str, page_no: int, box: list[float], target_width: int = 420) -> bytes:
    doc = pymupdf.open(path)
    try:
        page = doc[page_no - 1]
        rect = pymupdf.Rect(*box) & page.rect
        if rect.is_empty or rect.width < 2 or rect.height < 2:
            raise ParseError("裁剪区域无效。")
        dpi = max(96, min(300, int(72 * target_width / max(rect.width, 1))))
        pix = page.get_pixmap(clip=rect, dpi=dpi, alpha=False)
        return pix.tobytes("png")
    finally:
        doc.close()
