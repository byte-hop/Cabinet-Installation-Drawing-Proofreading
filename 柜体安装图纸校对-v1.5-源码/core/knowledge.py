from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator


ENGINE_VERSION = "1.3.0"
SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").split())


def file_fingerprint(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def issue_signature(template: str, issue: dict[str, Any]) -> str:
    raw = "\x1f".join(
        (
            _normalize(template),
            _normalize(issue.get("code")),
            _normalize(issue.get("title")),
        )
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class KnowledgeBase:
    """Local learning memory: document cache plus confirmed/dismissed issue rules."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self.enabled = False
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()
            self.enabled = True
        except Exception:
            self.enabled = False

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self._lock, self._db() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    fingerprint TEXT PRIMARY KEY,
                    engine_version TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    template TEXT NOT NULL,
                    template_name TEXT NOT NULL,
                    page_count INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    first_elapsed_ms REAL NOT NULL DEFAULT 0,
                    fastest_elapsed_ms REAL NOT NULL DEFAULT 0,
                    last_elapsed_ms REAL NOT NULL DEFAULT 0,
                    hits INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_used TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_documents_used
                    ON documents(last_used DESC);
                CREATE TABLE IF NOT EXISTS corrections (
                    signature TEXT PRIMARY KEY,
                    template TEXT NOT NULL,
                    template_name TEXT NOT NULL,
                    issue_code TEXT NOT NULL,
                    issue_title TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    hits INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_used TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_corrections_template
                    ON corrections(template, last_used DESC);
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def get_document(self, fingerprint: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        try:
            with self._lock, self._db() as connection:
                row = connection.execute(
                    "SELECT * FROM documents WHERE fingerprint=? AND engine_version=?",
                    (fingerprint, ENGINE_VERSION),
                ).fetchone()
            if row is None:
                return None
            payload = json.loads(row["payload_json"])
            return {
                "payload": payload,
                "first_elapsed_ms": float(row["first_elapsed_ms"] or 0),
                "fastest_elapsed_ms": float(row["fastest_elapsed_ms"] or 0),
                "last_elapsed_ms": float(row["last_elapsed_ms"] or 0),
                "hits": int(row["hits"] or 0),
                "file_name": row["file_name"],
                "template": row["template"],
                "last_used": row["last_used"],
            }
        except Exception:
            return None

    def save_document(
        self,
        fingerprint: str,
        payload: dict[str, Any],
        *,
        file_name: str,
        template: str,
        template_name: str,
        page_count: int,
        elapsed_ms: float,
    ) -> bool:
        if not self.enabled:
            return False
        try:
            stored = dict(payload)
            stored.pop("knowledge", None)
            stored["id"] = ""
            stored.pop("analyzed_at", None)
            serialized = json.dumps(stored, ensure_ascii=False, separators=(",", ":"))
            stamp = _now()
            with self._lock, self._db() as connection:
                connection.execute(
                    """
                    INSERT INTO documents(
                        fingerprint, engine_version, file_name, template, template_name,
                        page_count, payload_json, first_elapsed_ms, fastest_elapsed_ms,
                        last_elapsed_ms, hits, created_at, last_used
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?)
                    ON CONFLICT(fingerprint) DO UPDATE SET
                        engine_version=excluded.engine_version,
                        file_name=excluded.file_name,
                        template=excluded.template,
                        template_name=excluded.template_name,
                        page_count=excluded.page_count,
                        payload_json=excluded.payload_json,
                        first_elapsed_ms=excluded.first_elapsed_ms,
                        fastest_elapsed_ms=excluded.fastest_elapsed_ms,
                        last_elapsed_ms=excluded.last_elapsed_ms,
                        last_used=excluded.last_used
                    """,
                    (
                        fingerprint,
                        ENGINE_VERSION,
                        file_name,
                        template,
                        template_name,
                        page_count,
                        serialized,
                        float(elapsed_ms),
                        float(elapsed_ms),
                        float(elapsed_ms),
                        stamp,
                        stamp,
                    ),
                )
            return True
        except Exception:
            return False

    def mark_cache_hit(self, fingerprint: str, elapsed_ms: float) -> None:
        if not self.enabled:
            return
        try:
            stamp = _now()
            with self._lock, self._db() as connection:
                connection.execute(
                    """
                    UPDATE documents SET
                        hits=hits+1,
                        last_elapsed_ms=?,
                        fastest_elapsed_ms=CASE
                            WHEN fastest_elapsed_ms<=0 THEN ?
                            WHEN ?<fastest_elapsed_ms THEN ?
                            ELSE fastest_elapsed_ms END,
                        last_used=?
                    WHERE fingerprint=? AND engine_version=?
                    """,
                    (float(elapsed_ms), float(elapsed_ms), float(elapsed_ms), float(elapsed_ms), stamp, fingerprint, ENGINE_VERSION),
                )
        except Exception:
            return

    def corrections_for(self, template: str) -> dict[str, str]:
        if not self.enabled:
            return {}
        try:
            with self._lock, self._db() as connection:
                rows = connection.execute(
                    "SELECT signature, decision FROM corrections WHERE template=?",
                    (template,),
                ).fetchall()
            return {row["signature"]: row["decision"] for row in rows}
        except Exception:
            return {}

    def make_issue_filter(
        self, template: str
    ) -> tuple[Callable[[dict[str, Any]], bool], dict[str, Any]]:
        corrections = self.corrections_for(template)
        matched: list[str] = []
        info: dict[str, Any] = {
            "available": bool(corrections),
            "corrections": len(corrections),
            "dismissed": 0,
            "confirmed": 0,
            "matched": matched,
        }

        def apply(issue: dict[str, Any]) -> bool:
            signature = issue_signature(template, issue)
            issue["knowledge"] = {"signature": signature}
            decision = corrections.get(signature)
            if not decision:
                return True
            matched.append(signature)
            issue["knowledge"]["decision"] = decision
            if decision == "dismiss":
                info["dismissed"] += 1
                return False
            if decision == "confirm":
                info["confirmed"] += 1
            return True

        return apply, info

    def bump_corrections(self, signatures: list[str]) -> None:
        if not self.enabled or not signatures:
            return
        try:
            stamp = _now()
            with self._lock, self._db() as connection:
                connection.executemany(
                    "UPDATE corrections SET hits=hits+1, last_used=? WHERE signature=?",
                    [(stamp, signature) for signature in dict.fromkeys(signatures)],
                )
        except Exception:
            return

    def remember_issue(
        self,
        *,
        template: str,
        template_name: str,
        issue: dict[str, Any],
        decision: str,
        note: str = "",
    ) -> str | None:
        if not self.enabled or decision not in {"confirm", "dismiss"}:
            return None
        signature = issue_signature(template, issue)
        try:
            stamp = _now()
            with self._lock, self._db() as connection:
                connection.execute(
                    """
                    INSERT INTO corrections(
                        signature, template, template_name, issue_code, issue_title,
                        decision, note, hits, created_at, last_used
                    ) VALUES(?,?,?,?,?,?,?,0,?,?)
                    ON CONFLICT(signature) DO UPDATE SET
                        template=excluded.template,
                        template_name=excluded.template_name,
                        issue_code=excluded.issue_code,
                        issue_title=excluded.issue_title,
                        decision=excluded.decision,
                        note=excluded.note,
                        last_used=excluded.last_used
                    """,
                    (
                        signature,
                        template,
                        template_name,
                        _normalize(issue.get("code")),
                        _normalize(issue.get("title")),
                        decision,
                        note.strip(),
                        stamp,
                        stamp,
                    ),
                )
            return signature
        except Exception:
            return None

    def delete_correction(self, signature: str) -> bool:
        if not self.enabled:
            return False
        try:
            with self._lock, self._db() as connection:
                cursor = connection.execute("DELETE FROM corrections WHERE signature=?", (signature,))
            return cursor.rowcount > 0
        except Exception:
            return False

    def clear_documents(self) -> int:
        if not self.enabled:
            return 0
        try:
            with self._lock, self._db() as connection:
                cursor = connection.execute("DELETE FROM documents")
            return cursor.rowcount
        except Exception:
            return 0

    def clear_corrections(self) -> int:
        if not self.enabled:
            return 0
        try:
            with self._lock, self._db() as connection:
                cursor = connection.execute("DELETE FROM corrections")
            return cursor.rowcount
        except Exception:
            return 0

    def stats(self) -> dict[str, Any]:
        empty = {
            "enabled": self.enabled,
            "engine_version": ENGINE_VERSION,
            "documents": 0,
            "cache_hits": 0,
            "corrections": 0,
            "dismissed": 0,
            "confirmed": 0,
            "avg_first_ms": 0,
            "avg_cached_ms": 0,
            "speedup_pct": 0,
            "document_rows": [],
            "correction_rows": [],
        }
        if not self.enabled:
            return empty
        try:
            with self._lock, self._db() as connection:
                doc = connection.execute(
                    """
                    SELECT COUNT(*) AS documents,
                           COALESCE(SUM(hits), 0) AS cache_hits,
                           COALESCE(AVG(first_elapsed_ms), 0) AS avg_first_ms,
                           COALESCE(AVG(CASE WHEN hits>0 THEN last_elapsed_ms END), 0) AS avg_cached_ms
                    FROM documents WHERE engine_version=?
                    """,
                    (ENGINE_VERSION,),
                ).fetchone()
                corr = connection.execute(
                    """
                    SELECT COUNT(*) AS corrections,
                           COALESCE(SUM(decision='dismiss'), 0) AS dismissed,
                           COALESCE(SUM(decision='confirm'), 0) AS confirmed
                    FROM corrections
                    """
                ).fetchone()
                document_rows = connection.execute(
                    """
                    SELECT file_name, template, template_name, page_count, hits,
                           first_elapsed_ms, fastest_elapsed_ms, last_elapsed_ms, last_used
                    FROM documents WHERE engine_version=?
                    ORDER BY last_used DESC LIMIT 40
                    """,
                    (ENGINE_VERSION,),
                ).fetchall()
                correction_rows = connection.execute(
                    """
                    SELECT signature, template, template_name, issue_code, issue_title,
                           decision, note, hits, last_used
                    FROM corrections ORDER BY last_used DESC LIMIT 80
                    """
                ).fetchall()
            avg_first = float(doc["avg_first_ms"] or 0)
            avg_cached = float(doc["avg_cached_ms"] or 0)
            speedup = 0.0
            if avg_first > 0 and avg_cached > 0:
                speedup = max(0.0, (1.0 - avg_cached / avg_first) * 100.0)
            return {
                "enabled": True,
                "engine_version": ENGINE_VERSION,
                "documents": int(doc["documents"] or 0),
                "cache_hits": int(doc["cache_hits"] or 0),
                "corrections": int(corr["corrections"] or 0),
                "dismissed": int(corr["dismissed"] or 0),
                "confirmed": int(corr["confirmed"] or 0),
                "avg_first_ms": round(avg_first, 1),
                "avg_cached_ms": round(avg_cached, 1),
                "speedup_pct": round(speedup, 1),
                "document_rows": [dict(row) for row in document_rows],
                "correction_rows": [dict(row) for row in correction_rows],
            }
        except Exception:
            return empty
