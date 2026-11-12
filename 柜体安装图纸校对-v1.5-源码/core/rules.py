from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Callable

from .pdf_parser import DocumentModel, Step


SEVERITY_RANK = {"error": 0, "warn": 1, "info": 2}


def check_document(
    model: DocumentModel,
    issue_filter: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    usage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    step_sums: Counter[str] = Counter()

    def add_issue(
        severity: str,
        code: str,
        title: str,
        detail: str,
        suggestion: str,
        *,
        page: int | None = None,
        step: int | None = None,
        boxes: list[dict[str, Any]] | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        issue = {
            "severity": severity,
            "code": code,
            "title": title,
            "detail": detail,
            "suggestion": suggestion,
            "page": page,
            "step": step,
            "boxes": boxes or [],
            "evidence": evidence or {},
        }
        if issue_filter is not None and not issue_filter(issue):
            return
        issue["id"] = f"{code}:{len(issues) + 1}"
        issues.append(issue)

    def step_boxes(step: Step, code: str | None = None) -> list[dict[str, Any]]:
        if code:
            boxes = [
                {
                    "page": step.page,
                    "box": entry["box"],
                    "label": f"步骤 {step.no} · {code}",
                }
                for entry in step.hardware
                if entry["code"] == code
            ]
            if boxes:
                return boxes
        if step.table_box:
            return [
                {
                    "page": step.page,
                    "box": step.table_box,
                    "label": f"步骤 {step.no} 用量表",
                }
            ]
        return []

    for step in model.steps:
        per_step_codes: Counter[str] = Counter()
        for entry in step.hardware:
            code = entry["code"]
            step_sums[code] += entry["qty"]
            usage[code].append(
                {
                    "step": step.no,
                    "page": step.page,
                    "repeat": step.repeat,
                    "qty": entry["qty"],
                    "box": entry["box"],
                }
            )
            per_step_codes[code] += 1
        for code, count in per_step_codes.items():
            if count > 1:
                add_issue(
                    "error",
                    "HW_TABLE_DUP",
                    f"步骤 {step.no} 用量表中 {code} 重复出现",
                    f"同一编号在一张用量表里出现 {count} 次，容易造成合计重复计算。",
                    "合并为一行，只保留一个总数量。",
                    page=step.page,
                    step=step.no,
                    boxes=step_boxes(step, code),
                )

    bom_units = sum(item.qty for item in model.bom.values())
    hardware_units = sum(step_sums.values())

    for code, item in model.bom.items():
        used = step_sums.get(code, 0)
        diff = used - item.qty
        boxes = [{"page": item.page, "box": item.box, "label": f"五金 {code} 清单"}]
        for entry in usage.get(code, []):
            boxes.append(
                {
                    "page": entry["page"],
                    "box": entry["box"],
                    "label": f"步骤 {entry['step']} · {code}",
                }
            )
        if used == 0:
            add_issue(
                "error",
                "HW_UNUSED",
                f"五金 {code} 清单有 {item.qty} 件，但步骤中完全未使用",
                f"五金清单列出 {code} ×{item.qty}"
                + (f"（另附 {item.spare} 件备用）" if item.spare else "")
                + "，但所有步骤用量表都没有出现该编号。",
                "检查是否漏了安装步骤、用量表字母写错，或清单多列了该五金。",
                page=item.page,
                boxes=boxes,
                evidence={"bom": item.qty, "spare": item.spare, "steps": used, "diff": diff},
            )
            continue
        if diff == 0:
            continue
        if item.spare and used == item.qty + item.spare:
            detail = (
                f"步骤合计 {used} 件，正好等于清单 {item.qty}+{item.spare} 的含备用总量。"
                f"安装用量应按 {item.qty} 件核对，备用件不应计入步骤。"
            )
            suggestion = "确认用量表中是否把备用件算进了安装数量；若清单数量本身应含备用，请统一标注口径。"
        elif diff < 0:
            detail = (
                f"清单需求 {item.qty} 件，步骤用量表合计 {used} 件，少 {abs(diff)} 件。"
                f"可能是某个步骤用量写少、漏步骤，或清单数量多写。"
            )
            suggestion = "优先核对差额是否等于某个步骤的用量，再检查清单数量。"
        else:
            detail = (
                f"清单需求 {item.qty} 件，步骤用量表合计 {used} 件，多 {diff} 件。"
                f"可能是某个步骤用量写多、重复统计，或清单数量少写。"
            )
            suggestion = "检查是否有重复的步骤用量表，或把备用件计入了安装用量。"
        add_issue(
            "error",
            "HW_TOTAL_MISMATCH",
            f"五金 {code} 用量差 {diff:+d} 件",
            detail,
            suggestion,
            page=item.page,
            boxes=boxes,
            evidence={
                "bom": item.qty,
                "spare": item.spare,
                "steps": used,
                "diff": diff,
                "usage": usage.get(code, []),
            },
        )

    for code in sorted(step_sums):
        if code in model.bom:
            continue
        used_steps = sorted({entry["step"] for entry in usage[code]})
        boxes = []
        for entry in usage[code]:
            boxes.append(
                {
                    "page": entry["page"],
                    "box": entry["box"],
                    "label": f"步骤 {entry['step']} · {code}",
                }
            )
        add_issue(
            "error",
            "HW_UNKNOWN",
            f"步骤中使用未知五金编号 {code}",
            f"五金清单只有 {len(model.bom)} 类编号，没有 {code}。该编号可能是字母错标，或清单漏项。",
            "对照五金清单确认应使用的编号，并同步修改步骤用量表。",
            page=usage[code][0]["page"],
            step=used_steps[0] if used_steps else None,
            boxes=boxes,
            evidence={"steps": used_steps, "qty": step_sums[code]},
        )

    for step in model.steps:
        if step.repeat > 1:
            for entry in step.hardware:
                if entry["qty"] <= 0:
                    add_issue(
                        "warn",
                        "HW_QTY_ZERO",
                        f"步骤 {step.no} 中 {entry['code']} 用量为 0",
                        "用量表中的数量为 0，可能是占位或误写。",
                        "确认该五金是否真的不使用；不使用则删除该条目。",
                        page=step.page,
                        step=step.no,
                        boxes=step_boxes(step, entry["code"]),
                    )
                elif entry["qty"] % step.repeat != 0:
                    add_issue(
                        "warn",
                        "HW_QTY_NOT_DIVISIBLE",
                        f"步骤 {step.no} 的 {entry['code']} 用量不能被 ×{step.repeat} 整除",
                        f"步骤标注 ×{step.repeat}，用量表写 {entry['code']} ×{entry['qty']}，"
                        f"平均每套 {entry['qty'] / step.repeat:.2f} 件，数量或重复倍数可能有误。",
                        "确认该步骤是否真的重复，以及每套应使用的五金数量。",
                        page=step.page,
                        step=step.no,
                        boxes=step_boxes(step, entry["code"]),
                    )

    for step in model.steps:
        if not step.hardware:
            continue
        listed = {entry["code"] for entry in step.hardware}
        for code in sorted(listed):
            if step.callout_counts.get(code, 0) == 0:
                add_issue(
                    "info",
                    "HW_TABLE_NOT_DRAWN",
                    f"步骤 {step.no} 用量表列出 {code}，但图中未找到标注",
                    f"用量表有 {code}，但该步骤图形中没有找到 {code} 的字母标注。"
                    "若该五金以图形直接示意、不需要字母标注，可以忽略；否则应补标或核对字母。",
                    "对照图形确认该五金是否已表达清楚；需要引线标注时请补上。",
                    page=step.page,
                    step=step.no,
                    boxes=step_boxes(step, code),
                )
        for code in sorted(step.callout_counts):
            if code in listed or code not in model.bom:
                continue
            add_issue(
                "info",
                "HW_DRAWN_NOT_TABLE",
                f"步骤 {step.no} 图中出现 {code}，用量表未列",
                f"该步骤图形中有 {code} 标注，但用量表没有这一项。若它是局部示意图的说明标注，可以忽略。",
                "确认该标注是否参与本步骤安装；若参与，请补进用量表。",
                page=step.page,
                step=step.no,
                boxes=[
                    {
                        "page": step.page,
                        "box": _callout_box(step, code),
                        "label": f"步骤 {step.no} · {code}",
                    }
                ]
                if _callout_box(step, code)
                else [],
            )

    used_part_nos: set[int] = set()
    for step in model.steps:
        counts = Counter(int(panel["no"]) for panel in step.panels)
        for no, count in sorted(counts.items()):
            used_part_nos.add(no)
            item = model.parts.get(no)
            boxes = [
                {
                    "page": step.page,
                    "box": panel["box"],
                    "label": f"步骤 {step.no} · 板件 {no}",
                }
                for panel in step.panels
                if int(panel["no"]) == no
            ]
            if item is None:
                add_issue(
                    "error",
                    "PART_UNKNOWN",
                    f"步骤 {step.no} 出现未知板件编号 {no}",
                    f"板件清单最大编号为 {max(model.parts) if model.parts else 0}，没有 {no} 号板。"
                    "该编号可能是错标，或板件清单漏项。",
                    "对照板件清单确认该位置应使用的板件号，并同步修改图纸或清单。",
                    page=step.page,
                    step=step.no,
                    boxes=boxes,
                    evidence={"step_panels": step.panel_nos},
                )
                continue
            if item.qty <= 0:
                add_issue(
                    "error",
                    "PART_QTY_ZERO",
                    f"板件 {no} 清单数量为 0，但步骤中有使用",
                    f"板件 {no} 在清单中数量为 {item.qty}，步骤 {step.no} 却标注了它。",
                    "检查清单数量或步骤标注是否写错。",
                    page=step.page,
                    step=step.no,
                    boxes=boxes,
                )
                continue
            if item.qty < step.repeat:
                add_issue(
                    "error",
                    "PART_REPEAT_IMPOSSIBLE",
                    f"板件 {no} 数量不足以支持步骤 {step.no} 的 ×{step.repeat}",
                    f"板件 {no} 全柜只有 {item.qty} 件，但步骤 {step.no} 标注 ×{step.repeat}。"
                    f"若每套都用 {no} 号板，需要 {step.repeat} 件，数量矛盾。该编号很可能标错。",
                    f"核对该步骤的板件编号，确认是否应为同批 ×{step.repeat} 的板件；同时检查清单数量。",
                    page=step.page,
                    step=step.no,
                    boxes=boxes,
                    evidence={"part_qty": item.qty, "repeat": step.repeat},
                )
                continue
            if step.repeat > 1 and item.qty % step.repeat != 0:
                add_issue(
                    "warn",
                    "PART_REPEAT_ODD",
                    f"板件 {no} 用量与步骤 {step.no} 的 ×{step.repeat} 不匹配",
                    f"板件 {no} 共 {item.qty} 件，步骤按 ×{step.repeat} 重复，"
                    f"平均每套 {item.qty / step.repeat:.2f} 件，不是整数。",
                    "确认该步骤是否应使用该板件，或板件清单数量是否正确。",
                    page=step.page,
                    step=step.no,
                    boxes=boxes,
                )
                continue
            per_unit = item.qty // step.repeat
            if count > per_unit:
                add_issue(
                    "info",
                    "PART_DUP_CALLOUT",
                    f"步骤 {step.no} 中板件 {no} 标注 {count} 次",
                    f"清单中板件 {no} 为 {item.qty} 件"
                    + (f"，按 ×{step.repeat} 计算每套 {per_unit} 件" if step.repeat > 1 else "")
                    + f"；该步骤图里出现 {count} 次标注。"
                    "若为同一板件的爆炸图与装配图两个视图，可以忽略；若代表两块不同板件，则其中一处编号可能错标。",
                    "对照图形确认是否同一块板；如为不同板件，请修正其中一个编号。",
                    page=step.page,
                    step=step.no,
                    boxes=boxes,
                    evidence={"part_qty": item.qty, "repeat": step.repeat, "count": count},
                )

    for no, item in model.parts.items():
        if no in used_part_nos:
            continue
        overview_only = no in model.overview
        add_issue(
            "warn",
            "PART_UNUSED",
            f"板件 {no} 未出现在任何安装步骤中",
            f"板件清单列出 {no} ×{item.qty}"
            + ("，装配总览有标注，但安装步骤中没有引用。" if overview_only else "，装配总览和安装步骤中都没有引用。")
            + "可能是漏画步骤、漏标编号，或清单多列。",
            "确认该板件是否真的需要安装；若需要，请补充步骤或标注。",
            page=item.page,
            boxes=[{"page": item.page, "box": item.box, "label": f"板件 {no} 清单"}],
        )

    overview_set = set(model.overview)
    for no in sorted(overview_set):
        if no in model.parts:
            continue
        box = next((b for b in model.overview_boxes if b["no"] == no), None)
        add_issue(
            "error",
            "PART_OVERVIEW_UNKNOWN",
            f"装配总览出现未知板件编号 {no}",
            f"总览图中标注了 {no}，但板件清单没有这个编号。很可能是编号错标。",
            "核对总览图中的标注，改为清单中实际存在的板件号。",
            page=model.detected.get("bom_page"),
            boxes=[{"page": box["page"], "box": box["box"], "label": f"总览 · {no}"}]
            if box
            else [],
        )

    for no in sorted(model.parts):
        if no in overview_set:
            continue
        item = model.parts[no]
        add_issue(
            "warn",
            "PART_OVERVIEW_MISSING",
            f"板件 {no} 未在装配总览中标注",
            f"板件 {no} 在清单中，也出现在安装步骤中，但总览图没有标注该编号。",
            "如总览需要完整展示板件编号，请补标；若总览只展示部分板件，可忽略。",
            page=item.page,
            boxes=[{"page": item.page, "box": item.box, "label": f"板件 {no} 清单"}],
        )

    step_nos = [step.no for step in model.steps]
    seen_steps: Counter[int] = Counter(step_nos)
    for no, count in sorted(seen_steps.items()):
        if count > 1:
            dup_steps = [s for s in model.steps if s.no == no]
            add_issue(
                "error",
                "STEP_DUP",
                f"步骤编号 {no} 出现 {count} 次",
                "同一步骤号被多张步骤卡重复使用，引用时无法区分。",
                "改为连续且唯一的步骤编号。",
                page=dup_steps[0].page,
                step=no,
                boxes=[
                    {"page": s.page, "box": s.table_box, "label": f"步骤 {no}"}
                    for s in dup_steps
                    if s.table_box
                ],
            )
    if step_nos:
        missing = [n for n in range(1, max(step_nos) + 1) if n not in seen_steps]
        if missing:
            add_issue(
                "warn",
                "STEP_GAP",
                f"步骤编号缺少 {len(missing)} 个",
                "现有步骤编号为 1–{}，缺少：{}。".format(
                    max(step_nos), "、".join(str(n) for n in missing)
                ),
                "检查是否漏页或漏编号；步骤号应连续，方便装配与售后引用。",
                page=model.detected.get("parts_page"),
            )

    part_numbers = sorted(model.parts)
    if part_numbers:
        missing_parts = [n for n in range(1, max(part_numbers) + 1) if n not in model.parts]
        if missing_parts:
            add_issue(
                "warn",
                "PART_GAP",
                f"板件清单编号不连续，缺少 {len(missing_parts)} 个",
                "清单编号从 1 到 {}，缺少：{}。".format(
                    max(part_numbers), "、".join(str(n) for n in missing_parts)
                ),
                "确认是否漏列板件，或编号本身有意跳号。",
                page=model.detected.get("parts_page"),
            )

    for warning in model.parse_warnings:
        if warning.startswith("第 ") and "没有可提取的文字层" in warning:
            add_issue(
                "warn",
                "PAGE_NO_TEXT",
                warning.rstrip("。"),
                "该页可能是扫描件或文字已转曲，无法自动识别编号与用量。",
                "从 CAD 重新导出带文字层的 PDF，或对该页进行人工复核。",
                page=_page_from_warning(warning),
            )

    issues.sort(key=lambda item: (SEVERITY_RANK[item["severity"]], item.get("page") or 999, item.get("step") or 999))

    checks = _build_checks(model, issues, step_sums, bom_units, hardware_units)
    errors = sum(1 for item in issues if item["severity"] == "error")
    warnings = sum(1 for item in issues if item["severity"] == "warn")
    infos = sum(1 for item in issues if item["severity"] == "info")
    status = "fail" if errors else "review" if warnings else "pass"

    return {
        "issues": issues,
        "checks": checks,
        "summary": {
            "status": status,
            "errors": errors,
            "warnings": warnings,
            "infos": infos,
            "hardware_kinds": len(model.bom),
            "hardware_units": hardware_units,
            "bom_units": bom_units,
            "spare_units": sum(item.spare for item in model.bom.values()),
            "part_kinds": len(model.parts),
            "part_units": sum(item.qty for item in model.parts.values()),
            "step_count": len(model.steps),
            "table_count": sum(1 for step in model.steps if step.hardware),
            "repeat_steps": sum(1 for step in model.steps if step.repeat > 1),
        },
    }


def _callout_box(step: Step, code: str) -> list[float] | None:
    for entry in step.hardware:
        if entry["code"] == code:
            return entry["box"]
    return step.table_box


def _page_from_warning(warning: str) -> int | None:
    import re

    match = re.match(r"第\s*(\d+)\s*页", warning)
    return int(match.group(1)) if match else None


def _build_checks(
    model: DocumentModel,
    issues: list[dict[str, Any]],
    step_sums: Counter[str],
    bom_units: int,
    hardware_units: int,
) -> list[dict[str, Any]]:
    def by_codes(*codes: str) -> list[dict[str, Any]]:
        code_set = set(codes)
        return [issue for issue in issues if issue["code"] in code_set]

    def status(items: list[dict[str, Any]]) -> str:
        if any(item["severity"] == "error" for item in items):
            return "fail"
        if any(item["severity"] == "warn" for item in items):
            return "warn"
        return "pass"

    def make(
        key: str,
        name: str,
        summary: str,
        items: list[dict[str, Any]],
        hint: str,
    ) -> dict[str, Any]:
        return {
            "key": key,
            "name": name,
            "summary": summary,
            "status": status(items),
            "issue_count": len(items),
            "hint": hint,
        }

    hw_total_items = by_codes("HW_TOTAL_MISMATCH", "HW_UNUSED")
    if hw_total_items:
        hw_total_summary = f"{len(hw_total_items)} 类五金与清单不一致"
    else:
        hw_total_summary = f"{len(model.bom)} 类五金用量合计 {hardware_units} 件，与清单 {bom_units} 件完全一致"

    hw_id_items = by_codes("HW_UNKNOWN")
    hw_step_items = by_codes("HW_TABLE_DUP", "HW_QTY_NOT_DIVISIBLE", "HW_QTY_ZERO")
    hw_presence_items = by_codes("HW_TABLE_NOT_DRAWN")
    part_id_items = by_codes("PART_UNKNOWN", "PART_QTY_ZERO")
    part_repeat_items = by_codes("PART_REPEAT_IMPOSSIBLE", "PART_REPEAT_ODD")
    part_cover_items = by_codes("PART_UNUSED", "PART_OVERVIEW_MISSING", "PART_OVERVIEW_UNKNOWN")
    step_items = by_codes("STEP_DUP", "STEP_GAP")
    data_items = by_codes("PAGE_NO_TEXT")

    part_used = len({int(panel["no"]) for step in model.steps for panel in step.panels})
    repeat_count = sum(1 for step in model.steps if step.repeat > 1)

    return [
        make(
            "hw_total",
            "五金用量合计",
            hw_total_summary,
            hw_total_items,
            "逐项累加各步骤用量表，与五金清单的“需求数量”比较，备用数量单独展示。",
        ),
        make(
            "hw_ids",
            "五金编号合法性",
            f"{len(model.bom)} 类五金编号均能在清单中找到"
            if not hw_id_items
            else f"{len(hw_id_items)} 个步骤使用了清单外的五金编号",
            hw_id_items,
            "步骤用量表中的字母必须存在于五金清单，防止字母错标。",
        ),
        make(
            "hw_step",
            "步骤用量表结构",
            f"{sum(1 for s in model.steps if s.hardware)} 张用量表全部通过进位与重复倍数检查"
            if not hw_step_items
            else f"{len(hw_step_items)} 张用量表存在数量结构问题",
            hw_step_items,
            "检查同一编号重复出现、数量为 0、以及数量不能被 ×N 整除的情况。",
        ),
        make(
            "hw_presence",
            "用量表与图形标注",
            "所有用量表五金均能在对应步骤图中找到标注"
            if not hw_presence_items
            else f"用量表五金与图形标注基本对应（{len(hw_presence_items)} 条提示）",
            hw_presence_items,
            "用量表列出的五金，图中应至少出现一次圆圈字母标注。",
        ),
        make(
            "part_ids",
            "板件编号合法性",
            f"{len(model.parts)} 类板件编号全部有效，无清单外编号"
            if not part_id_items
            else f"{len(part_id_items)} 个步骤使用了清单外的板件编号",
            part_id_items,
            "安装步骤与装配总览中的板件编号必须存在于板件清单。",
        ),
        make(
            "part_repeat",
            "板件用量与重复模块",
            f"{repeat_count} 个重复步骤的板件用量均与清单数量相容"
            if not part_repeat_items
            else f"{len(part_repeat_items)} 处板件用量与 ×N 重复步骤矛盾",
            part_repeat_items,
            "按 ×N 重复的步骤，每套都使用的板件，清单数量必须不少于 N 且能被 N 整除。",
        ),
        make(
            "part_coverage",
            "板件清单覆盖",
            f"{part_used} / {len(model.parts)} 类板件在安装步骤中被引用"
            if not part_cover_items
            else f"{len(part_cover_items)} 处板件引用不完整",
            part_cover_items,
            "清单中的板件应至少出现在一个步骤中；总览与步骤的编号应互相对应。",
        ),
        make(
            "step_seq",
            "步骤编号连续性",
            f"步骤编号 {min(s.no for s in model.steps)}–{max(s.no for s in model.steps)} 连续"
            if not step_items
            else f"{len(step_items)} 处步骤编号异常",
            step_items,
            "步骤号应连续且唯一，方便安装与售后按步骤沟通。",
        ),
        make(
            "data_quality",
            "识别数据完整性",
            "所有页面均含可提取文字层"
            if not data_items
            else f"{len(data_items)} 页缺少可提取文字层",
            data_items,
            "文字转曲或扫描页无法自动核对，需要人工复核或从 CAD 重新导出。",
        ),
    ]


def find_usage(model: DocumentModel, code: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for step in model.steps:
        for entry in step.hardware:
            if entry["code"] == code:
                rows.append(
                    {
                        "step": step.no,
                        "page": step.page,
                        "repeat": step.repeat,
                        "qty": entry["qty"],
                        "box": entry["box"],
                    }
                )
    return rows
