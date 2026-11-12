from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

FROZEN = bool(getattr(sys, "frozen", False))
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) if FROZEN else Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
SAMPLE_PDFS = {
    "lingkewu": BASE_DIR / "示例" / "领客屋.pdf",
    "aosom": BASE_DIR / "示例" / "遨森.pdf",
    "yingchi": BASE_DIR / "示例" / "英驰.pdf",
}
SAMPLE_NAMES = {
    "lingkewu": "领客屋模板",
    "aosom": "遨森模板",
    "yingchi": "英驰模板",
}
DEFAULT_SAMPLE = next(iter(SAMPLE_PDFS))

if FROZEN:
    data_candidates = [
        Path(sys.executable).resolve().parent / "data",
        Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CabinetDocCheck" / "data",
    ]
else:
    data_candidates = [BASE_DIR / "data"]

DATA_DIR = None
for candidate in data_candidates:
    try:
        for subdir in ("uploads", "cache", "exports", "knowledge", "widget"):
            (candidate / subdir).mkdir(parents=True, exist_ok=True)
        DATA_DIR = candidate
        break
    except OSError:
        continue
if DATA_DIR is None:
    raise RuntimeError("无法创建数据目录，请将程序放到有写入权限的位置。")

UPLOAD_DIR = DATA_DIR / "uploads"
CACHE_DIR = DATA_DIR / "cache"
EXPORT_DIR = DATA_DIR / "exports"

sys.path.insert(0, str(BASE_DIR))

from core.excel_export import build_report  # noqa: E402
from core.knowledge import ENGINE_VERSION as KNOWLEDGE_ENGINE_VERSION  # noqa: E402
from core.knowledge import KnowledgeBase, file_fingerprint  # noqa: E402
from core.pdf_parser import DocumentModel, ParseError, crop_image, page_image, parse_pdf  # noqa: E402
from core.rules import check_document  # noqa: E402


KNOWLEDGE_DIR = DATA_DIR / "knowledge"
KB = KnowledgeBase(KNOWLEDGE_DIR / "knowledge.db")

WIDGET_IMAGE_DIR = DATA_DIR / "widget"
WIDGET_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
WIDGET_SETTINGS_FILE = WIDGET_IMAGE_DIR / "settings.json"
WIDGET_DEFAULT_SIZE = 64
WIDGET_MIN_SIZE = 44
WIDGET_MAX_SIZE = 160

MAIN_WINDOW: Any = None
WIDGET_WINDOW: Any = None
ALLOW_APP_CLOSE = False
WIDGET_VISIBLE = False
WIDGET_MODE = "idle"
WIDGET_IDLE_SIZE = WIDGET_DEFAULT_SIZE
WIDGET_DROP_SIZE = (360, 112)
WIDGET_MENU_SIZE = (288, 224)
WIDGET_MAX_IMAGE_BYTES = 12 * 1024 * 1024
WIDGET_MINIMIZE_LOCK = threading.Lock()
WIDGET_SETTINGS_LOCK = threading.RLock()


@dataclass
class DocEntry:
    id: str
    path: str
    name: str
    model: Any
    payload: dict[str, Any]
    created: float = field(default_factory=time.time)


DOCS: dict[str, DocEntry] = {}
DOCS_LOCK = threading.Lock()
MAX_DOCS = 8
APP_TITLE = "柜图校对"
APP_VERSION = "1.5"


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _run_checks(model: DocumentModel) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    template = str(model.detected.get("template") or "unknown")
    template_name = str(model.detected.get("template_name") or template)
    issue_filter, knowledge_info = KB.make_issue_filter(template)
    result = check_document(model, issue_filter=issue_filter)
    KB.bump_corrections(knowledge_info.get("matched") or [])
    return result, knowledge_info, template, template_name


def _knowledge_meta(
    *,
    cache_hit: bool,
    elapsed_ms: float,
    first_elapsed_ms: float,
    info: dict[str, Any],
) -> dict[str, Any]:
    speedup = 0.0
    if cache_hit and first_elapsed_ms > 0:
        speedup = max(0.0, (1.0 - elapsed_ms / first_elapsed_ms) * 100.0)
    return {
        "enabled": KB.enabled,
        "engine_version": KNOWLEDGE_ENGINE_VERSION,
        "cache_hit": cache_hit,
        "elapsed_ms": round(float(elapsed_ms), 1),
        "first_elapsed_ms": round(float(first_elapsed_ms), 1),
        "speedup_pct": round(speedup, 1),
        "dismissed": int(info.get("dismissed") or 0),
        "confirmed": int(info.get("confirmed") or 0),
        "corrections": int(info.get("corrections") or 0),
    }


def _store_doc(path: str, name: str, refresh: bool = False) -> DocEntry:
    started = time.perf_counter()
    fingerprint = ""
    try:
        fingerprint = file_fingerprint(path)
    except OSError:
        pass

    cached = None if refresh or not fingerprint else KB.get_document(fingerprint)
    cache_hit = bool(cached)
    first_elapsed_ms = 0.0
    if cached:
        try:
            model = DocumentModel.from_payload(cached["payload"])
            model.file_name = name
            model.file_size = os.path.getsize(path)
            first_elapsed_ms = float(cached.get("first_elapsed_ms") or 0)
        except Exception:
            cache_hit = False
            cached = None
    if not cached:
        model = parse_pdf(path, name)

    result, knowledge_info, template, template_name = _run_checks(model)
    payload = model.to_payload()
    payload.update(result)
    payload["id"] = ""
    payload["analyzed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    elapsed_ms = (time.perf_counter() - started) * 1000

    if cache_hit and fingerprint:
        KB.mark_cache_hit(fingerprint, elapsed_ms)
    elif fingerprint:
        KB.save_document(
            fingerprint,
            payload,
            file_name=name,
            template=template,
            template_name=template_name,
            page_count=model.page_count,
            elapsed_ms=elapsed_ms,
        )
        first_elapsed_ms = elapsed_ms

    entry = DocEntry(id=_new_id(), path=path, name=name, model=model, payload=payload)
    payload["id"] = entry.id
    payload["knowledge"] = _knowledge_meta(
        cache_hit=cache_hit,
        elapsed_ms=elapsed_ms,
        first_elapsed_ms=first_elapsed_ms,
        info=knowledge_info,
    )
    with DOCS_LOCK:
        DOCS[entry.id] = entry
        if len(DOCS) > MAX_DOCS:
            oldest = min(DOCS.values(), key=lambda item: item.created)
            DOCS.pop(oldest.id, None)
            _cleanup_doc_files(oldest.id)
    return entry


def _refresh_entry(entry: DocEntry) -> dict[str, Any]:
    result, knowledge_info, _template, _template_name = _run_checks(entry.model)
    payload = entry.model.to_payload()
    payload.update(result)
    payload["id"] = entry.id
    payload["analyzed_at"] = entry.payload.get("analyzed_at") or datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    previous = entry.payload.get("knowledge") or {}
    meta = _knowledge_meta(
        cache_hit=bool(previous.get("cache_hit")),
        elapsed_ms=float(previous.get("elapsed_ms") or 0),
        first_elapsed_ms=float(previous.get("first_elapsed_ms") or 0),
        info=knowledge_info,
    )
    meta["feedback_refresh"] = True
    payload["knowledge"] = meta
    entry.payload = payload
    return payload


def _refresh_loaded_docs() -> None:
    with DOCS_LOCK:
        entries = list(DOCS.values())
    for entry in entries:
        try:
            _refresh_entry(entry)
        except Exception:
            continue


def _cleanup_doc_files(doc_id: str) -> None:
    for directory in (CACHE_DIR, EXPORT_DIR):
        for file in directory.glob(f"{doc_id}*"):
            try:
                file.unlink()
            except OSError:
                pass


def _get_doc(doc_id: str) -> DocEntry | None:
    with DOCS_LOCK:
        return DOCS.get(doc_id)


class Handler(BaseHTTPRequestHandler):
    server_version = "CabinetDocCheck/1.5"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("CABINET_CHECK_VERBOSE"):
            super().log_message(fmt, *args)

    def _send_bytes(
        self,
        data: bytes,
        content_type: str,
        status: int = 200,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(data, "application/json; charset=utf-8", status)

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": True, "message": message}, status)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if path == "/api/health":
            self._json({"ok": True, "docs": len(DOCS)})
            return
        if path == "/api/knowledge":
            self._json(KB.stats())
            return
        if path == "/api/widget/avatar":
            self._handle_widget_avatar()
            return
        if path == "/api/widget/info":
            self._json(_widget_info())
            return
        if path == "/api/page":
            self._handle_page(query)
            return
        if path == "/api/crop":
            self._handle_crop(query)
            return
        if path == "/api/export":
            self._handle_export(query)
            return
        if path == "/api/result":
            self._handle_result(query)
            return
        if path == "/api/sample-info":
            samples = []
            for sample_id, sample_path in SAMPLE_PDFS.items():
                samples.append(
                    {
                        "id": sample_id,
                        "name": sample_path.name,
                        "label": SAMPLE_NAMES[sample_id],
                        "available": sample_path.exists(),
                        "size": sample_path.stat().st_size if sample_path.exists() else 0,
                    }
                )
            self._json({"available": any(item["available"] for item in samples), "samples": samples})
            return
        self._serve_static(path)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/analyze":
            self._handle_analyze(parsed)
            return
        if parsed.path == "/api/widget/settings":
            self._handle_widget_settings()
            return
        if parsed.path == "/api/feedback":
            self._handle_feedback()
            return
        if parsed.path.startswith("/api/knowledge/"):
            self._handle_knowledge_action(parsed.path.rsplit("/", 1)[-1])
            return
        self._error("接口不存在。", HTTPStatus.NOT_FOUND)

    def _handle_analyze(self, parsed: urllib.parse.ParseResult) -> None:
        query = urllib.parse.parse_qs(parsed.query)
        try:
            refresh = query.get("refresh", ["0"])[0] == "1"
            sample_arg = query.get("sample", ["0"])[0]
            if sample_arg and sample_arg != "0":
                sample_id = sample_arg if sample_arg in SAMPLE_PDFS else DEFAULT_SAMPLE
                sample_path = SAMPLE_PDFS[sample_id]
                if not sample_path.exists():
                    self._error(f"示例图纸 {sample_path.name} 不存在。", HTTPStatus.NOT_FOUND)
                    return
                entry = _store_doc(str(sample_path), f"{sample_path.name}（示例）", refresh=refresh)
                self._json(entry.payload)
                return

            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                self._error("没有收到 PDF 文件。")
                return
            if length > 120 * 1024 * 1024:
                self._error("文件超过 120 MB，请拆分后再试。", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            raw_name = self.headers.get("X-Filename", "drawing.pdf")
            name = urllib.parse.unquote(raw_name)
            name = Path(name).name or "drawing.pdf"
            if not name.lower().endswith(".pdf"):
                name += ".pdf"
            doc_id = _new_id()
            target = UPLOAD_DIR / f"{doc_id}.pdf"
            remaining = length
            with target.open("wb") as handle:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    handle.write(chunk)
                    remaining -= len(chunk)
            if target.stat().st_size < 1024:
                target.unlink(missing_ok=True)
                self._error("上传的文件为空或不是有效 PDF。")
                return
            entry = _store_doc(str(target), name, refresh=refresh)
            self._json(entry.payload)
        except ParseError as exc:
            self._error(str(exc), HTTPStatus.UNPROCESSABLE_ENTITY)
        except Exception as exc:
            self._error(f"解析失败：{type(exc).__name__}: {exc}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def _read_json_body(self, limit: int = 128 * 1024) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("没有收到请求内容")
        if length > limit:
            raise ValueError("请求内容过大")
        raw = self.rfile.read(length)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("请求格式不正确")
        return data

    def _handle_widget_settings(self) -> None:
        try:
            patch = self._read_json_body(4096)
        except Exception as exc:
            self._error(f"设置数据无效：{exc}", HTTPStatus.BAD_REQUEST)
            return
        self._json(_apply_widget_settings(patch))

    def _handle_feedback(self) -> None:
        try:
            data = self._read_json_body()
        except Exception as exc:
            self._error(f"反馈数据无效：{exc}", HTTPStatus.BAD_REQUEST)
            return
        doc_id = str(data.get("doc_id") or "")
        issue_id = str(data.get("issue_id") or "")
        decision = str(data.get("decision") or "")
        note = str(data.get("note") or "")[:300]
        if decision not in {"confirm", "dismiss"}:
            self._error("反馈类型必须是 confirm 或 dismiss。", HTTPStatus.BAD_REQUEST)
            return
        entry = _get_doc(doc_id)
        if entry is None:
            self._error("文档已过期，请重新打开图纸。", HTTPStatus.NOT_FOUND)
            return
        issue = next(
            (item for item in entry.payload.get("issues") or [] if str(item.get("id")) == issue_id),
            None,
        )
        if issue is None:
            self._error("这条结果已不存在，请重新打开图纸后再试。", HTTPStatus.NOT_FOUND)
            return
        template = str(entry.model.detected.get("template") or "unknown")
        template_name = str(entry.model.detected.get("template_name") or template)
        signature = KB.remember_issue(
            template=template,
            template_name=template_name,
            issue=issue,
            decision=decision,
            note=note,
        )
        if not signature:
            self._error("知识库写入失败，请检查 data 目录是否可写。", HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        payload = _refresh_entry(entry)
        payload["feedback"] = {
            "decision": decision,
            "signature": signature,
            "issue_id": issue_id,
        }
        self._json(payload)

    def _handle_knowledge_action(self, action: str) -> None:
        try:
            data = self._read_json_body(16 * 1024)
        except Exception:
            data = {}
        if action == "delete":
            signature = str(data.get("signature") or "")
            if not signature:
                self._error("缺少需要删除的知识条目。", HTTPStatus.BAD_REQUEST)
                return
            count = 1 if KB.delete_correction(signature) else 0
            ok = bool(count)
        elif action in {"clear-cache", "clear-documents"}:
            count = KB.clear_documents()
            ok = True
        elif action in {"clear-corrections", "clear-learning"}:
            count = KB.clear_corrections()
            ok = True
        else:
            self._error("知识库操作不存在。", HTTPStatus.NOT_FOUND)
            return
        _refresh_loaded_docs()
        self._json({"ok": ok, "count": count, "knowledge": KB.stats()})

    def _handle_page(self, query: dict[str, list[str]]) -> None:
        try:
            doc_id = query["id"][0]
            page_no = int(query["page"][0])
            dpi = max(72, min(220, int(query.get("dpi", ["150"])[0])))
        except (KeyError, ValueError, IndexError):
            self._error("分页参数不完整。")
            return
        entry = _get_doc(doc_id)
        if entry is None:
            self._error("文档已过期，请重新打开。", HTTPStatus.NOT_FOUND)
            return
        if page_no < 1 or page_no > entry.model.page_count:
            self._error("页码超出范围。", HTTPStatus.NOT_FOUND)
            return
        cache_path = CACHE_DIR / f"{doc_id}_p{page_no}_{dpi}.png"
        if not cache_path.exists():
            data = page_image(entry.path, page_no, dpi)
            cache_path.write_bytes(data)
        self._send_bytes(
            cache_path.read_bytes(),
            "image/png",
            extra_headers={"Cache-Control": "public, max-age=3600"},
        )

    def _handle_crop(self, query: dict[str, list[str]]) -> None:
        try:
            doc_id = query["id"][0]
            page_no = int(query["page"][0])
            x0 = float(query["x0"][0])
            y0 = float(query["y0"][0])
            x1 = float(query["x1"][0])
            y1 = float(query["y1"][0])
            width = max(80, min(1000, int(query.get("w", ["460"])[0])))
        except (KeyError, ValueError, IndexError):
            self._error("裁剪参数不完整。")
            return
        entry = _get_doc(doc_id)
        if entry is None:
            self._error("文档已过期，请重新打开。", HTTPStatus.NOT_FOUND)
            return
        try:
            data = crop_image(entry.path, page_no, [x0, y0, x1, y1], width)
        except Exception as exc:
            self._error(f"裁剪失败：{exc}")
            return
        self._send_bytes(data, "image/png", extra_headers={"Cache-Control": "public, max-age=3600"})

    def _handle_export(self, query: dict[str, list[str]]) -> None:
        try:
            doc_id = query["id"][0]
        except (KeyError, IndexError):
            self._error("缺少文档编号。")
            return
        entry = _get_doc(doc_id)
        if entry is None:
            self._error("文档已过期，请重新打开。", HTTPStatus.NOT_FOUND)
            return
        target = EXPORT_DIR / f"{doc_id}.xlsx"
        if not target.exists():
            try:
                build_report(entry.model, entry.payload, str(target))
            except Exception as exc:
                self._error(f"生成报告失败：{exc}", HTTPStatus.INTERNAL_SERVER_ERROR)
                return
        data = target.read_bytes()
        base = Path(entry.name).stem
        filename = f"{base}_核对报告.xlsx"
        encoded = urllib.parse.quote(filename)
        self._send_bytes(
            data,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            extra_headers={
                "Content-Disposition": f"attachment; filename=report.xlsx; filename*=UTF-8''{encoded}",
                "Cache-Control": "no-store",
            },
        )

    def _handle_result(self, query: dict[str, list[str]]) -> None:
        try:
            doc_id = query["id"][0]
        except (KeyError, IndexError):
            self._error("缺少文档编号。")
            return
        entry = _get_doc(doc_id)
        if entry is None:
            self._error("文档已过期，请重新打开。", HTTPStatus.NOT_FOUND)
            return
        self._json(entry.payload)

    def _handle_widget_avatar(self) -> None:
        target = _widget_avatar_path()
        if target is None or not target.exists():
            self._error("悬浮球图片不存在。", HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(target))[0] or "image/png"
        self._send_bytes(
            target.read_bytes(),
            content_type,
            extra_headers={"Cache-Control": "no-store"},
        )

    def _serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            target = WEB_DIR / "index.html"
        else:
            relative = urllib.parse.unquote(path.lstrip("/"))
            target = (WEB_DIR / relative).resolve()
            try:
                target.relative_to(WEB_DIR.resolve())
            except ValueError:
                self._error("非法路径。", HTTPStatus.FORBIDDEN)
                return
        if not target.exists() or not target.is_file():
            self._error("文件不存在。", HTTPStatus.NOT_FOUND)
            return
        content_type, _ = mimetypes.guess_type(str(target))
        if target.suffix.lower() == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif target.suffix.lower() == ".css":
            content_type = "text/css; charset=utf-8"
        elif target.suffix.lower() == ".html":
            content_type = "text/html; charset=utf-8"
        content_type = content_type or "application/octet-stream"
        self._send_bytes(target.read_bytes(), content_type, extra_headers={"Cache-Control": "no-cache"})


def _find_port(start: int) -> int:
    for port in range(start, start + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("找不到可用端口。")


def _prepare_desktop_runtime() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CabinetDocCheck.Desktop")
    except Exception:
        pass
    browser_args = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "").strip()
    if "--no-sandbox" not in browser_args.split():
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"{browser_args} --no-sandbox".strip()


def _show_error(message: str) -> None:
    title = f"{APP_TITLE} 启动失败"
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
            return
        except Exception:
            pass
    print(f"{title}\n{message}", file=sys.stderr)


def _write_crash_log() -> Path | None:
    try:
        path = DATA_DIR / "error.log"
        path.write_text(traceback.format_exc(), encoding="utf-8")
        return path
    except Exception:
        return None


def _detect_widget_image_suffix(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"BM"):
        return ".bmp"
    return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _clamp_widget_size(value: Any) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return WIDGET_DEFAULT_SIZE
    return max(WIDGET_MIN_SIZE, min(WIDGET_MAX_SIZE, size))


def _read_widget_settings() -> dict[str, Any]:
    settings = {"size": WIDGET_DEFAULT_SIZE, "close_to_widget": False}
    with WIDGET_SETTINGS_LOCK:
        try:
            if WIDGET_SETTINGS_FILE.exists():
                raw = json.loads(WIDGET_SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    settings["size"] = _clamp_widget_size(raw.get("size", WIDGET_DEFAULT_SIZE))
                    settings["close_to_widget"] = _as_bool(raw.get("close_to_widget", False))
        except Exception:
            pass
    return settings


def _write_widget_settings(patch: dict[str, Any]) -> dict[str, Any]:
    with WIDGET_SETTINGS_LOCK:
        settings = _read_widget_settings()
        if "size" in patch:
            settings["size"] = _clamp_widget_size(patch.get("size"))
        if "close_to_widget" in patch:
            settings["close_to_widget"] = _as_bool(patch.get("close_to_widget"))
        try:
            temporary = WIDGET_SETTINGS_FILE.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(settings, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(WIDGET_SETTINGS_FILE)
        except OSError:
            pass
        return settings


WIDGET_IDLE_SIZE = int(_read_widget_settings()["size"])


def _widget_avatar_path() -> Path | None:
    candidates = [path for path in WIDGET_IMAGE_DIR.glob("avatar.*") if path.is_file()]
    if candidates:
        return max(candidates, key=lambda path: path.stat().st_mtime_ns)
    fallback = WEB_DIR / "widget-avatar.png"
    return fallback if fallback.exists() else None


def _widget_avatar_info() -> dict[str, Any]:
    target = _widget_avatar_path()
    if target is None:
        return {"ok": False, "custom": False, "url": "", "version": 0}
    is_custom = target.parent == WIDGET_IMAGE_DIR
    version = target.stat().st_mtime_ns
    return {
        "ok": True,
        "custom": is_custom,
        "url": f"/api/widget/avatar?v={version}",
        "version": version,
        "name": target.name,
    }


def _widget_settings_info() -> dict[str, Any]:
    settings = _read_widget_settings()
    return {
        "size": int(settings["size"]),
        "size_min": WIDGET_MIN_SIZE,
        "size_max": WIDGET_MAX_SIZE,
        "size_default": WIDGET_DEFAULT_SIZE,
        "close_to_widget": bool(settings["close_to_widget"]),
    }


def _widget_info() -> dict[str, Any]:
    info = dict(_widget_avatar_info())
    info.update(_widget_settings_info())
    return info


def _apply_widget_settings(patch: dict[str, Any]) -> dict[str, Any]:
    global WIDGET_IDLE_SIZE
    if not isinstance(patch, dict):
        patch = {}
    with WIDGET_SETTINGS_LOCK:
        old_size = WIDGET_IDLE_SIZE
        settings = _write_widget_settings(patch)
        WIDGET_IDLE_SIZE = int(settings["size"])
        size_changed = WIDGET_IDLE_SIZE != old_size
    if (
        WIDGET_WINDOW is not None
        and WIDGET_VISIBLE
        and WIDGET_MODE == "idle"
        and size_changed
    ):
        _set_widget_mode("idle")
    info = _widget_info()
    info["ok"] = True
    info["message"] = "悬浮球设置已保存。"
    return info


def _save_widget_image(source: str) -> dict[str, Any]:
    path = Path(source)
    if not path.is_file():
        return {"ok": False, "message": "选择的图片不存在。"}
    try:
        size = path.stat().st_size
        if size <= 0 or size > WIDGET_MAX_IMAGE_BYTES:
            return {"ok": False, "message": "图片需小于 12 MB。"}
        data = path.read_bytes()
    except OSError as exc:
        return {"ok": False, "message": f"读取图片失败：{exc}"}
    suffix = _detect_widget_image_suffix(data)
    if suffix is None:
        return {"ok": False, "message": "请选择 PNG、JPG、WEBP、GIF 或 BMP 图片。"}
    try:
        for old in WIDGET_IMAGE_DIR.glob("avatar.*"):
            old.unlink(missing_ok=True)
        target = WIDGET_IMAGE_DIR / f"avatar{suffix}"
        target.write_bytes(data)
    except OSError as exc:
        return {"ok": False, "message": f"保存图片失败：{exc}"}
    info = _widget_info()
    info["message"] = "悬浮球图片已更新。"
    return info


def _primary_work_area() -> tuple[int, int, int, int]:
    if sys.platform != "win32":
        return 0, 0, 1440, 900
    try:
        import ctypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rect = RECT()
        if not ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return 0, 0, 1440, 900
        scale = 1.0
        try:
            scale = max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96.0)
        except Exception:
            pass
        return (
            int(rect.left / scale),
            int(rect.top / scale),
            int(rect.right / scale),
            int(rect.bottom / scale),
        )
    except Exception:
        return 0, 0, 1440, 900


def _widget_handle() -> int | None:
    if sys.platform != "win32" or WIDGET_WINDOW is None:
        return None
    try:
        native = getattr(WIDGET_WINDOW, "native", None)
        handle = getattr(native, "Handle", None)
        if handle is None:
            return None
        return handle.ToInt64() if hasattr(handle, "ToInt64") else int(handle)
    except Exception:
        return None


def _widget_dpi_scale(hwnd: int) -> float:
    try:
        import ctypes

        return max(1.0, ctypes.windll.user32.GetDpiForWindow(hwnd) / 96.0)
    except Exception:
        return 1.0


def _apply_widget_region(mode: str) -> None:
    if sys.platform != "win32":
        return
    hwnd = _widget_handle()
    if hwnd is None:
        return
    mode = str(mode or "idle")
    if mode in {"drop", "busy", "error", "success"}:
        width, height = WIDGET_DROP_SIZE
        radius = height / 2
    elif mode == "menu":
        width, height = WIDGET_MENU_SIZE
        radius = 22
    else:
        width = height = WIDGET_IDLE_SIZE
        radius = WIDGET_IDLE_SIZE / 2
    try:
        import ctypes

        scale = _widget_dpi_scale(hwnd)
        physical_width = max(1, int(round(width * scale)))
        physical_height = max(1, int(round(height * scale)))
        diameter = max(2, int(round(radius * 2 * scale)))
        gdi32 = ctypes.windll.gdi32
        region = gdi32.CreateRoundRectRgn(
            0,
            0,
            physical_width + 1,
            physical_height + 1,
            diameter,
            diameter,
        )
        if region:
            if not ctypes.windll.user32.SetWindowRgn(hwnd, region, True):
                gdi32.DeleteObject(region)
    except Exception:
        pass


def _set_widget_mode(mode: str) -> None:
    global WIDGET_MODE
    if WIDGET_WINDOW is None:
        return
    mode = str(mode or "idle")
    if mode not in {"idle", "drop", "busy", "error", "success", "menu"}:
        mode = "idle"
    WIDGET_MODE = mode
    if mode in {"drop", "busy", "error", "success"}:
        width, height = WIDGET_DROP_SIZE
    elif mode == "menu":
        width, height = WIDGET_MENU_SIZE
    else:
        width, height = WIDGET_IDLE_SIZE, WIDGET_IDLE_SIZE
    try:
        from webview.window import FixPoint

        WIDGET_WINDOW.resize(width, height, FixPoint.EAST | FixPoint.SOUTH)
    except Exception:
        pass
    _apply_widget_region(mode)


def _mark_widget_as_tool_window() -> None:
    hwnd = _widget_handle()
    if hwnd is None or sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_APPWINDOW = 0x00040000
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
    except Exception:
        pass


def _show_widget() -> None:
    global WIDGET_VISIBLE
    if WIDGET_WINDOW is None:
        return
    try:
        WIDGET_WINDOW.show()
        _mark_widget_as_tool_window()
        WIDGET_VISIBLE = True
        _set_widget_mode("idle")
        WIDGET_WINDOW.evaluate_js("window.cabinetResetWidget && window.cabinetResetWidget()")
    except Exception:
        pass


def _hide_widget() -> None:
    global WIDGET_VISIBLE
    WIDGET_VISIBLE = False
    if WIDGET_WINDOW is None:
        return
    try:
        WIDGET_WINDOW.hide()
    except Exception:
        pass


def _hide_main() -> None:
    if MAIN_WINDOW is None:
        return
    try:
        MAIN_WINDOW.hide()
    except Exception:
        pass


def _restore_main() -> None:
    _hide_widget()
    if MAIN_WINDOW is None:
        return
    try:
        MAIN_WINDOW.show()
        MAIN_WINDOW.restore()
    except Exception:
        pass


def _exit_app() -> None:
    global ALLOW_APP_CLOSE, WIDGET_VISIBLE
    ALLOW_APP_CLOSE = True
    WIDGET_VISIBLE = False
    for window in (WIDGET_WINDOW, MAIN_WINDOW):
        if window is None:
            continue
        try:
            window.hide()
        except Exception:
            pass
        try:
            window.destroy()
        except Exception:
            pass


def _on_main_closing(window: Any) -> bool:
    if ALLOW_APP_CLOSE:
        return True
    if _read_widget_settings()["close_to_widget"]:
        threading.Thread(target=_minimize_flow, daemon=True).start()
        return False

    def show_prompt() -> None:
        try:
            window.evaluate_js("window.cabinetShowClosePrompt && window.cabinetShowClosePrompt()")
        except Exception:
            pass

    threading.Thread(target=show_prompt, daemon=True).start()
    return False


def _on_widget_closing(window: Any) -> bool:
    if ALLOW_APP_CLOSE:
        return True
    threading.Thread(target=_restore_main, daemon=True).start()
    return False


def _on_main_minimized(*_args: Any) -> None:
    threading.Thread(target=_show_widget, daemon=True).start()


def _on_main_restored(*_args: Any) -> None:
    threading.Thread(target=_hide_widget, daemon=True).start()


def _complete_widget_result(doc_id: str) -> None:
    _restore_main()
    if MAIN_WINDOW is None or not doc_id:
        return
    time.sleep(0.15)
    try:
        MAIN_WINDOW.evaluate_js(
            f"window.cabinetLoadResultById && window.cabinetLoadResultById({json.dumps(doc_id)})"
        )
    except Exception:
        pass


def _choose_widget_image() -> dict[str, Any]:
    parent = WIDGET_WINDOW if WIDGET_VISIBLE else MAIN_WINDOW
    if parent is None:
        return {"ok": False, "message": "程序窗口尚未就绪。"}
    try:
        import webview

        selected = parent.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("图片 (*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp)",),
        )
    except Exception as exc:
        return {"ok": False, "message": f"无法打开图片选择窗口：{exc}"}
    if not selected:
        return {"ok": False, "canceled": True}
    return _save_widget_image(selected[0])


def _minimize_flow() -> None:
    if not WIDGET_MINIMIZE_LOCK.acquire(blocking=False):
        return
    try:
        _show_widget()
        time.sleep(0.15)
        _hide_main()
    finally:
        WIDGET_MINIMIZE_LOCK.release()


class DesktopApi:
    def minimize_to_widget(self) -> dict[str, Any]:
        threading.Thread(target=_minimize_flow, daemon=True).start()
        return {"ok": True}

    def restore_main(self) -> dict[str, Any]:
        threading.Thread(target=_restore_main, daemon=True).start()
        return {"ok": True}

    def exit_app(self) -> dict[str, Any]:
        threading.Thread(target=_exit_app, daemon=True).start()
        return {"ok": True}

    def widget_mode(self, mode: str) -> dict[str, Any]:
        _set_widget_mode(str(mode))
        return {"ok": True, "mode": str(mode)}

    def choose_widget_image(self) -> dict[str, Any]:
        return _choose_widget_image()

    def update_widget_settings(self, patch: dict[str, Any] | None = None) -> dict[str, Any]:
        return _apply_widget_settings(patch or {})

    def show_result(self, doc_id: str) -> dict[str, Any]:
        threading.Thread(target=_complete_widget_result, args=(str(doc_id),), daemon=True).start()
        return {"ok": True}


def _start_server(port: int) -> tuple[ThreadingHTTPServer, str]:
    actual_port = _find_port(port)
    server = ThreadingHTTPServer(("127.0.0.1", actual_port), Handler)
    return server, f"http://127.0.0.1:{actual_port}/"


def _create_desktop_windows(webview: Any, url: str) -> tuple[DesktopApi, Any, Any]:
    global MAIN_WINDOW, WIDGET_WINDOW
    api = DesktopApi()
    main_window = webview.create_window(
        APP_TITLE,
        url,
        width=1440,
        height=900,
        min_size=(1100, 720),
        background_color="#eef2f7",
        text_select=True,
        js_api=api,
    )
    MAIN_WINDOW = main_window
    if main_window is not None:
        main_window.events.closing += _on_main_closing
        main_window.events.minimized += _on_main_minimized
        main_window.events.restored += _on_main_restored

    left, top, right, bottom = _primary_work_area()
    widget_x = max(left, right - WIDGET_IDLE_SIZE - 28)
    widget_y = max(top, bottom - WIDGET_IDLE_SIZE - 54)
    widget_window = webview.create_window(
        "柜图校对悬浮球",
        urllib.parse.urljoin(url, "widget.html"),
        width=WIDGET_IDLE_SIZE,
        height=WIDGET_IDLE_SIZE,
        x=widget_x,
        y=widget_y,
        min_size=(WIDGET_MIN_SIZE, WIDGET_MIN_SIZE),
        resizable=False,
        hidden=True,
        frameless=True,
        easy_drag=True,
        shadow=False,
        focus=False,
        on_top=True,
        background_color="#000000",
        transparent=True,
        text_select=False,
        js_api=api,
    )
    WIDGET_WINDOW = widget_window
    if widget_window is not None:
        widget_window.events.closing += _on_widget_closing
    return api, main_window, widget_window


def _run_desktop(url: str) -> int:
    _prepare_desktop_runtime()
    try:
        import webview
    except Exception as exc:
        _show_error(
            "无法加载桌面窗口组件。\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "请重新安装完整版程序，或安装 Microsoft Edge WebView2 Runtime。"
        )
        return 1

    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    _create_desktop_windows(webview, url)
    webview.start(
        gui="edgechromium",
        private_mode=True,
        storage_path=str(DATA_DIR / "webview"),
    )
    return 0


def _configure_windows_console() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
        kernel32.SetConsoleTitleW("柜图校对")
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> None:
    _configure_windows_console()
    parser = argparse.ArgumentParser(description="柜图校对 - 柜体安装图五金与板件核对")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--browser", action="store_true", help="使用系统浏览器打开（诊断模式）")
    parser.add_argument("--no-browser", action="store_true", help="只启动本地服务，不打开窗口")
    args = parser.parse_args()

    server, url = _start_server(args.port)
    print("=" * 62)
    print(f"  {APP_TITLE} v{APP_VERSION}  Cabinet Doc Check")
    print(f"  本地服务：{url}")
    print("  全部解析在本机完成，图纸不会上传。")
    print("=" * 62)

    if args.no_browser:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return

    if args.browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        exit_code = _run_desktop(url)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log_path = _write_crash_log()
        detail = f"\n\n错误日志：{log_path}" if log_path else ""
        _show_error(f"程序发生未处理错误，请重新启动。{detail}")
        raise
