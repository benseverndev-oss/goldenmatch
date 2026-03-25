"""Confidence-gated review queue for GoldenCheck's DQ agent."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from goldencheck.models.finding import Finding, Severity

__all__ = ["ReviewItem", "ReviewQueue"]


@dataclass
class ReviewItem:
    """A single item in the review queue."""

    job_name: str
    item_id: str
    column: str
    check: str
    severity: str
    confidence: float
    message: str
    explanation: str = ""
    sample_values: list[str] = field(default_factory=list)
    status: str = "pending"
    decided_by: str = ""
    decided_at: datetime | None = None


# ---------------------------------------------------------------------------
# Backend interface
# ---------------------------------------------------------------------------


class _Backend(ABC):
    """Common interface for review-queue storage backends."""

    @abstractmethod
    def add(self, item: ReviewItem) -> None: ...

    @abstractmethod
    def get_pending(self, job_name: str) -> list[ReviewItem]: ...

    @abstractmethod
    def update_status(
        self,
        item_id: str,
        status: str,
        decided_by: str,
        reason: str,
    ) -> None: ...

    @abstractmethod
    def get_stats(self, job_name: str) -> dict[str, int]: ...


# ---------------------------------------------------------------------------
# Memory backend
# ---------------------------------------------------------------------------


class _MemoryBackend(_Backend):
    """In-memory dict backend — no persistence."""

    def __init__(self) -> None:
        self._items: dict[str, ReviewItem] = {}

    def add(self, item: ReviewItem) -> None:
        self._items[item.item_id] = item

    def get_pending(self, job_name: str) -> list[ReviewItem]:
        return [
            it
            for it in self._items.values()
            if it.job_name == job_name and it.status == "pending"
        ]

    def update_status(
        self,
        item_id: str,
        status: str,
        decided_by: str,
        reason: str,  # noqa: ARG002
    ) -> None:
        item = self._items.get(item_id)
        if item is None:
            msg = f"ReviewItem {item_id!r} not found"
            raise KeyError(msg)
        item.status = status
        item.decided_by = decided_by
        item.decided_at = datetime.now(timezone.utc)

    def get_stats(self, job_name: str) -> dict[str, int]:
        counts: dict[str, int] = {"pending": 0, "pinned": 0, "dismissed": 0}
        for it in self._items.values():
            if it.job_name == job_name and it.status in counts:
                counts[it.status] += 1
        return counts


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------

_SQLITE_SCHEMA_VERSION = 1

_SQLITE_CREATE = """\
CREATE TABLE IF NOT EXISTS reviews (
    item_id TEXT PRIMARY KEY,
    job_name TEXT NOT NULL,
    column_name TEXT NOT NULL,
    check_name TEXT NOT NULL,
    severity TEXT NOT NULL,
    confidence REAL NOT NULL,
    message TEXT NOT NULL,
    explanation TEXT DEFAULT '',
    sample_values TEXT DEFAULT '[]',
    status TEXT DEFAULT 'pending',
    decided_by TEXT DEFAULT '',
    decided_at TEXT
);
"""


class _SQLiteBackend(_Backend):
    """SQLite backend — stores reviews in ``.goldencheck/reviews.db``."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = Path(".goldencheck") / "reviews.db"
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            "PRAGMA user_version",
        )
        version = cur.fetchone()[0]
        if version < _SQLITE_SCHEMA_VERSION:
            cur.executescript(_SQLITE_CREATE)
            cur.execute(f"PRAGMA user_version = {_SQLITE_SCHEMA_VERSION}")
            self._conn.commit()

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> ReviewItem:
        decided_at = None
        if row["decided_at"]:
            decided_at = datetime.fromisoformat(row["decided_at"])
        return ReviewItem(
            job_name=row["job_name"],
            item_id=row["item_id"],
            column=row["column_name"],
            check=row["check_name"],
            severity=row["severity"],
            confidence=row["confidence"],
            message=row["message"],
            explanation=row["explanation"],
            sample_values=json.loads(row["sample_values"]),
            status=row["status"],
            decided_by=row["decided_by"],
            decided_at=decided_at,
        )

    def add(self, item: ReviewItem) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO reviews "
            "(item_id, job_name, column_name, check_name, severity, confidence, "
            "message, explanation, sample_values, status, decided_by, decided_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item.item_id,
                item.job_name,
                item.column,
                item.check,
                item.severity,
                item.confidence,
                item.message,
                item.explanation,
                json.dumps(item.sample_values),
                item.status,
                item.decided_by,
                item.decided_at.isoformat() if item.decided_at else None,
            ),
        )
        self._conn.commit()

    def get_pending(self, job_name: str) -> list[ReviewItem]:
        cur = self._conn.execute(
            "SELECT * FROM reviews WHERE job_name = ? AND status = 'pending'",
            (job_name,),
        )
        return [self._row_to_item(row) for row in cur.fetchall()]

    def update_status(
        self,
        item_id: str,
        status: str,
        decided_by: str,
        reason: str,  # noqa: ARG002
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "UPDATE reviews SET status = ?, decided_by = ?, decided_at = ? "
            "WHERE item_id = ?",
            (status, decided_by, now, item_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            msg = f"ReviewItem {item_id!r} not found"
            raise KeyError(msg)

    def get_stats(self, job_name: str) -> dict[str, int]:
        cur = self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM reviews "
            "WHERE job_name = ? GROUP BY status",
            (job_name,),
        )
        counts: dict[str, int] = {"pending": 0, "pinned": 0, "dismissed": 0}
        for row in cur.fetchall():
            if row["status"] in counts:
                counts[row["status"]] = row["cnt"]
        return counts


# ---------------------------------------------------------------------------
# Postgres backend
# ---------------------------------------------------------------------------


class _PostgresBackend(_Backend):
    """Postgres backend — table ``goldencheck._reviews``."""

    def __init__(self, dsn: str) -> None:
        try:
            import psycopg2  # type: ignore[import-untyped]
        except ImportError as exc:
            msg = (
                "psycopg2 is required for Postgres backend. "
                "Install it with: pip install psycopg2-binary"
            )
            raise ImportError(msg) from exc

        self._conn = psycopg2.connect(dsn)
        self._conn.autocommit = True
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS goldencheck")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS goldencheck._reviews (
                    item_id TEXT PRIMARY KEY,
                    job_name TEXT NOT NULL,
                    column_name TEXT NOT NULL,
                    check_name TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    confidence DOUBLE PRECISION NOT NULL,
                    message TEXT NOT NULL,
                    explanation TEXT DEFAULT '',
                    sample_values TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'pending',
                    decided_by TEXT DEFAULT '',
                    decided_at TIMESTAMPTZ,
                    schema_version INTEGER DEFAULT 1
                )
                """
            )

    def _row_to_item(self, row: tuple) -> ReviewItem:
        decided_at = row[11]
        return ReviewItem(
            item_id=row[0],
            job_name=row[1],
            column=row[2],
            check=row[3],
            severity=row[4],
            confidence=row[5],
            message=row[6],
            explanation=row[7],
            sample_values=json.loads(row[8]) if isinstance(row[8], str) else row[8],
            status=row[9],
            decided_by=row[10],
            decided_at=decided_at,
        )

    def add(self, item: ReviewItem) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO goldencheck._reviews
                    (item_id, job_name, column_name, check_name, severity,
                     confidence, message, explanation, sample_values,
                     status, decided_by, decided_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (item_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    decided_by = EXCLUDED.decided_by,
                    decided_at = EXCLUDED.decided_at
                """,
                (
                    item.item_id,
                    item.job_name,
                    item.column,
                    item.check,
                    item.severity,
                    item.confidence,
                    item.message,
                    item.explanation,
                    json.dumps(item.sample_values),
                    item.status,
                    item.decided_by,
                    item.decided_at,
                ),
            )

    def get_pending(self, job_name: str) -> list[ReviewItem]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM goldencheck._reviews "
                "WHERE job_name = %s AND status = 'pending'",
                (job_name,),
            )
            return [self._row_to_item(row) for row in cur.fetchall()]

    def update_status(
        self,
        item_id: str,
        status: str,
        decided_by: str,
        reason: str,  # noqa: ARG002
    ) -> None:
        now = datetime.now(timezone.utc)
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE goldencheck._reviews "
                "SET status = %s, decided_by = %s, decided_at = %s "
                "WHERE item_id = %s",
                (status, decided_by, now, item_id),
            )
            if cur.rowcount == 0:
                msg = f"ReviewItem {item_id!r} not found"
                raise KeyError(msg)

    def get_stats(self, job_name: str) -> dict[str, int]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT status, COUNT(*) FROM goldencheck._reviews "
                "WHERE job_name = %s GROUP BY status",
                (job_name,),
            )
            counts: dict[str, int] = {"pending": 0, "pinned": 0, "dismissed": 0}
            for status, cnt in cur.fetchall():
                if status in counts:
                    counts[status] = cnt
            return counts


# ---------------------------------------------------------------------------
# Public ReviewQueue
# ---------------------------------------------------------------------------

_SEVERITY_MAP = {
    "ERROR": Severity.ERROR,
    "WARNING": Severity.WARNING,
    "INFO": Severity.INFO,
}


class ReviewQueue:
    """Confidence-gated review queue with pluggable storage backends."""

    def __init__(self, backend: str = "auto") -> None:
        self._backend = self._resolve_backend(backend)

    @staticmethod
    def _resolve_backend(backend: str) -> _Backend:
        if backend == "auto":
            if os.environ.get("DATABASE_URL"):
                try:
                    return _PostgresBackend(os.environ["DATABASE_URL"])
                except ImportError:
                    pass
            if Path(".goldencheck").is_dir():
                return _SQLiteBackend()
            return _MemoryBackend()

        if backend == "memory":
            return _MemoryBackend()
        if backend == "sqlite":
            return _SQLiteBackend()
        if backend == "postgres":
            dsn = os.environ.get("DATABASE_URL", "")
            if not dsn:
                msg = "DATABASE_URL env var required for postgres backend"
                raise ValueError(msg)
            return _PostgresBackend(dsn)

        msg = f"Unknown backend: {backend!r}"
        raise ValueError(msg)

    # -- public API ---------------------------------------------------------

    def add(self, item: ReviewItem) -> None:
        """Add an item to the review queue."""
        self._backend.add(item)

    def pending(self, job_name: str) -> list[ReviewItem]:
        """Return all pending items for *job_name*."""
        return self._backend.get_pending(job_name)

    def approve(self, item_id: str, decided_by: str, reason: str = "") -> None:
        """Mark an item as pinned (approved)."""
        self._backend.update_status(item_id, "pinned", decided_by, reason)

    def reject(self, item_id: str, decided_by: str, reason: str = "") -> None:
        """Mark an item as dismissed (rejected)."""
        self._backend.update_status(item_id, "dismissed", decided_by, reason)

    def stats(self, job_name: str) -> dict[str, int]:
        """Return counts by status for *job_name*."""
        return self._backend.get_stats(job_name)

    # -- gating logic -------------------------------------------------------

    def classify_findings(
        self,
        findings: list[Finding],
        job_name: str,
    ) -> dict[str, list[ReviewItem]]:
        """Gate findings by confidence and severity.

        * confidence >= 0.8 AND severity >= WARNING  -> auto_pinned
        * 0.5 <= confidence < 0.8 AND severity >= WARNING -> review_queue
        * confidence < 0.5 OR severity == INFO -> auto_dismissed
        """
        pinned: list[ReviewItem] = []
        review: list[ReviewItem] = []
        dismissed: list[ReviewItem] = []

        for finding in findings:
            item = _finding_to_review_item(finding, job_name)
            high_severity = finding.severity >= Severity.WARNING

            if finding.confidence >= 0.8 and high_severity:
                item.status = "pinned"
                pinned.append(item)
            elif finding.confidence >= 0.5 and high_severity:
                item.status = "pending"
                self._backend.add(item)
                review.append(item)
            else:
                item.status = "dismissed"
                dismissed.append(item)

        return {"pinned": pinned, "review": review, "dismissed": dismissed}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _finding_to_review_item(finding: Finding, job_name: str) -> ReviewItem:
    """Convert a Finding to a ReviewItem."""
    return ReviewItem(
        job_name=job_name,
        item_id=uuid.uuid4().hex,
        column=finding.column,
        check=finding.check,
        severity=finding.severity.name,
        confidence=finding.confidence,
        message=finding.message,
        explanation=finding.suggestion or "",
        sample_values=list(finding.sample_values),
    )
