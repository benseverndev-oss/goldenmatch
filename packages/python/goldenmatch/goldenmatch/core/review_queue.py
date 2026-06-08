"""Confidence-gated review queue for human-in-the-loop pair decisions."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import sqlite3

    import polars as pl

    from goldenmatch.core.memory.store import MemoryStore

log = logging.getLogger("goldenmatch.memory")


@dataclass
class ReviewItem:
    """A single pair awaiting human review."""

    job_name: str
    id_a: int
    id_b: int
    score: float
    explanation: str
    status: Literal["pending", "approved", "rejected"] = "pending"
    decided_by: str | None = None
    decided_at: str | None = None
    reason: str | None = None
    why: str | None = None

    def __post_init__(self) -> None:
        # Normalize empty `why` to None so callers don't have to special-case
        # the difference between "no explanation" and "explainer ran but
        # produced an empty string".
        if not self.why:
            self.why = None

    def approve(self, decided_by: str) -> None:
        self.status = "approved"
        self.decided_by = decided_by
        self.decided_at = datetime.now(UTC).isoformat()

    def reject(self, decided_by: str, reason: str = "") -> None:
        self.status = "rejected"
        self.decided_by = decided_by
        self.decided_at = datetime.now(UTC).isoformat()
        self.reason = reason


_MAX_WHY_CHARS = 240


def _row_to_dict(df: pl.DataFrame, row_id: int, fields: list[str]) -> dict:
    """Pluck a row's matchkey-field values into a dict; empty if not found."""
    try:
        cols = [f for f in fields if f in df.columns]
        if "__row_id__" not in df.columns or not cols:
            return {}
        sub = df.filter(df["__row_id__"] == row_id).select(cols)
        if sub.is_empty():
            return {}
        return sub.row(0, named=True)
    except Exception as e:
        log.debug("row lookup for id=%s failed: %s", row_id, e)
        return {}


def _llm_enabled() -> bool:
    import os
    return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))


def why_for_correction(
    id_a: int,
    id_b: int,
    df: pl.DataFrame | None,
    matchkey_fields: list[str] | None,
    *,
    score: float = 0.0,
    use_llm: bool = False,
) -> str:
    """Compute a short ``why`` string for a (id_a, id_b) correction.

    Default path is deterministic (zero cost). When ``use_llm=True`` AND an
    LLM API key is present in the environment, routes through
    :func:`goldenmatch.core.llm_scorer.llm_explain_pair`. Falls back to the
    deterministic path on any error so callers never have to handle
    exceptions. Output is always non-empty and clipped to one short sentence.
    """
    row_a: dict = {}
    row_b: dict = {}
    fields = list(matchkey_fields) if matchkey_fields else []
    if df is not None and fields:
        row_a = _row_to_dict(df, id_a, fields)
        row_b = _row_to_dict(df, id_b, fields)

    if use_llm and _llm_enabled():
        try:
            from goldenmatch.core.llm_scorer import llm_explain_pair
            out = llm_explain_pair(row_a or {}, row_b or {}, score)
            if out:
                if len(out) > _MAX_WHY_CHARS:
                    out = out[: _MAX_WHY_CHARS - 3].rstrip() + "..."
                return out
        except Exception as e:  # pragma: no cover - defensive
            log.warning("why_for_correction LLM path failed: %s", e)

    # Deterministic path.
    try:
        if row_a and row_b and fields:
            field_scores = []
            for f in fields:
                va = row_a.get(f)
                vb = row_b.get(f)
                if va is None and vb is None:
                    continue
                # Cheap exact-or-not signal; explain_pair_nl knows how to
                # phrase it. We don't compute fuzzy scores here -- this stays
                # zero-cost.
                s = 1.0 if (va is not None and str(va) == str(vb)) else 0.4
                field_scores.append({
                    "field": f, "scorer": "exact",
                    "value_a": va, "value_b": vb,
                    "score": s, "weight": 1.0,
                })
            if field_scores:
                from goldenmatch.core.explain import explain_pair_nl
                out = explain_pair_nl(row_a, row_b, field_scores, score)
                out = (out or "").strip().replace("\n", " ")
                if out:
                    if len(out) > _MAX_WHY_CHARS:
                        out = out[: _MAX_WHY_CHARS - 3].rstrip() + "..."
                    return out
    except Exception as e:  # pragma: no cover - defensive
        log.warning("why_for_correction deterministic path failed: %s", e)

    return f"Pair ({id_a}, {id_b}) flagged at score {score:.2f}."


def _quality_gated_review_enabled() -> bool:
    """Door #5: route high-score pairs built on GoldenCheck-flagged cells to
    review instead of auto-merge. OFF by default (the #662 kill-switch pattern);
    enable with GOLDENMATCH_QUALITY_GATED_REVIEW=1.
    Spec: docs/design/2026-06-07-quality-gated-review-design.md."""
    import os

    val = os.environ.get("GOLDENMATCH_QUALITY_GATED_REVIEW")
    return val is not None and val.lower() in ("1", "true", "yes", "on")


def gate_pairs(
    pairs: list[tuple[int, int, float]],
    merge_threshold: float = 0.95,
    review_threshold: float = 0.75,
    *,
    row_quality: dict[int, float] | None = None,
    quality_floor: float = 0.7,
    reasons: dict[tuple[int, int], str] | None = None,
) -> tuple[list[tuple[int, int, float]], list[tuple[int, int, float]], list[tuple[int, int, float]]]:
    """Split scored pairs into auto-merged, review, and auto-rejected buckets.

    Parameters
    ----------
    pairs : list of (id_a, id_b, score) tuples
    merge_threshold : scores strictly above this are auto-merged
    review_threshold : scores >= this (and <= merge_threshold) go to review
    row_quality : optional ``{row_id: worst_cell_quality}`` (door #5). When
        provided, a pair that would auto-merge but whose worse record falls below
        ``quality_floor`` is **downgraded to review** instead — a confident score
        on GoldenCheck-flagged data is exactly what a steward should see. This is
        **downgrade-only**: review/reject pairs are never touched (zero recall
        cost), and ``row_quality=None`` is byte-identical to the original gating.
    quality_floor : a pair is held for review when ``min(q_a, q_b) < quality_floor``.
    reasons : optional mutable dict; when passed, downgraded pairs get a
        human-readable provenance string keyed by ``(id_a, id_b)`` (door #6).

    Returns
    -------
    (auto_merged, review, auto_rejected) tuple of lists
    """
    auto_merged: list[tuple[int, int, float]] = []
    review: list[tuple[int, int, float]] = []
    auto_rejected: list[tuple[int, int, float]] = []

    for id_a, id_b, score in pairs:
        if score > merge_threshold:
            if row_quality is not None:
                q = min(row_quality.get(id_a, 1.0), row_quality.get(id_b, 1.0))
                if q < quality_floor:
                    review.append((id_a, id_b, score))
                    if reasons is not None:
                        reasons[(id_a, id_b)] = (
                            f"auto-merge held for review: a matched record has a "
                            f"low-quality value (quality {q:.2f} < {quality_floor:.2f}, "
                            f"flagged by GoldenCheck) — the high score may rest on "
                            f"suspect data."
                        )
                    continue
            auto_merged.append((id_a, id_b, score))
        elif score >= review_threshold:
            review.append((id_a, id_b, score))
        else:
            auto_rejected.append((id_a, id_b, score))

    return auto_merged, review, auto_rejected


class _MemoryBackend:
    """In-memory storage for review items."""

    def __init__(self) -> None:
        self._jobs: dict[str, list[ReviewItem]] = {}

    def add(self, item: ReviewItem) -> None:
        self._jobs.setdefault(item.job_name, []).append(item)

    def list_pending(self, job_name: str) -> list[ReviewItem]:
        return [it for it in self._jobs.get(job_name, []) if it.status == "pending"]

    def _find(self, job_name: str, id_a: int, id_b: int) -> ReviewItem | None:
        for it in self._jobs.get(job_name, []):
            if it.id_a == id_a and it.id_b == id_b and it.status == "pending":
                return it
        return None

    def approve(self, job_name: str, id_a: int, id_b: int, decided_by: str) -> None:
        item = self._find(job_name, id_a, id_b)
        if item:
            item.approve(decided_by)

    def reject(self, job_name: str, id_a: int, id_b: int, decided_by: str, reason: str = "") -> None:
        item = self._find(job_name, id_a, id_b)
        if item:
            item.reject(decided_by, reason)

    def stats(self, job_name: str) -> dict[str, int]:
        items = self._jobs.get(job_name, [])
        return {
            "pending": sum(1 for it in items if it.status == "pending"),
            "approved": sum(1 for it in items if it.status == "approved"),
            "rejected": sum(1 for it in items if it.status == "rejected"),
        }


class _SQLiteBackend:
    """SQLite-backed persistent storage for review items."""

    def __init__(self, path: str | None = None) -> None:
        if path is None:
            db_dir = Path(".goldenmatch")
            db_dir.mkdir(exist_ok=True)
            self._db_path = db_dir / "reviews.db"
        else:
            self._db_path = Path(path)
            parent = self._db_path.parent
            if str(parent) and parent != Path(""):
                parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        import sqlite3  # noqa: PLC0415 -- lazy, see #364
        return sqlite3.connect(str(self._db_path))

    def _init_db(self) -> None:
        con = self._connect()
        try:
            con.execute(
                """CREATE TABLE IF NOT EXISTS reviews (
                    job_name TEXT NOT NULL,
                    id_a INTEGER NOT NULL,
                    id_b INTEGER NOT NULL,
                    score REAL NOT NULL,
                    explanation TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    decided_by TEXT,
                    decided_at TEXT,
                    reason TEXT,
                    PRIMARY KEY (job_name, id_a, id_b)
                )"""
            )
            con.commit()
        finally:
            con.close()

    def add(self, item: ReviewItem) -> None:
        con = self._connect()
        try:
            con.execute(
                "INSERT OR REPLACE INTO reviews (job_name, id_a, id_b, score, explanation, status, decided_by, decided_at, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (item.job_name, item.id_a, item.id_b, item.score, item.explanation, item.status, item.decided_by, item.decided_at, item.reason),
            )
            con.commit()
        finally:
            con.close()

    def list_pending(self, job_name: str) -> list[ReviewItem]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT job_name, id_a, id_b, score, explanation, status, decided_by, decided_at, reason FROM reviews WHERE job_name = ? AND status = 'pending'",
                (job_name,),
            ).fetchall()
            return [
                ReviewItem(
                    job_name=r[0], id_a=r[1], id_b=r[2], score=r[3],
                    explanation=r[4], status=r[5], decided_by=r[6],
                    decided_at=r[7], reason=r[8],
                )
                for r in rows
            ]
        finally:
            con.close()

    def approve(self, job_name: str, id_a: int, id_b: int, decided_by: str) -> None:
        now = datetime.now(UTC).isoformat()
        con = self._connect()
        try:
            con.execute(
                "UPDATE reviews SET status = 'approved', decided_by = ?, decided_at = ? WHERE job_name = ? AND id_a = ? AND id_b = ? AND status = 'pending'",
                (decided_by, now, job_name, id_a, id_b),
            )
            con.commit()
        finally:
            con.close()

    def reject(self, job_name: str, id_a: int, id_b: int, decided_by: str, reason: str = "") -> None:
        now = datetime.now(UTC).isoformat()
        con = self._connect()
        try:
            con.execute(
                "UPDATE reviews SET status = 'rejected', decided_by = ?, decided_at = ?, reason = ? WHERE job_name = ? AND id_a = ? AND id_b = ? AND status = 'pending'",
                (decided_by, now, reason, job_name, id_a, id_b),
            )
            con.commit()
        finally:
            con.close()

    def stats(self, job_name: str) -> dict[str, int]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT status, COUNT(*) FROM reviews WHERE job_name = ? GROUP BY status",
                (job_name,),
            ).fetchall()
            counts = {"pending": 0, "approved": 0, "rejected": 0}
            for status, cnt in rows:
                if status in counts:
                    counts[status] = cnt
            return counts
        finally:
            con.close()


class ReviewQueue:
    """Confidence-gated review queue with pluggable backends.

    Parameters
    ----------
    backend : str
        "memory" (default) or "sqlite"
    """

    def __init__(
        self,
        backend: str = "memory",
        path: str | None = None,
        *,
        memory_store: MemoryStore | None = None,
        df: pl.DataFrame | None = None,
        matchkey_fields: list[str] | None = None,
        dataset: str | None = None,
        use_llm_explainer: bool = False,
    ) -> None:
        if backend == "memory":
            self._backend = _MemoryBackend()
        elif backend == "sqlite":
            self._backend = _SQLiteBackend(path=path)
        else:
            raise ValueError(f"Unknown backend: {backend!r}. Use 'memory' or 'sqlite'.")
        self._backend_name = backend
        self._memory_store = memory_store
        self._memory_df = df
        self._memory_matchkey_fields = list(matchkey_fields) if matchkey_fields else None
        self._memory_dataset = dataset
        self._use_llm_explainer = use_llm_explainer

    def close(self) -> None:
        """Close any backend resources. SQLite uses per-call connections, so this is a no-op."""
        return None

    @property
    def storage_tier(self) -> str:
        return self._backend_name

    def add(self, job_name: str, id_a: int, id_b: int, score: float, explanation: str) -> None:
        item = ReviewItem(job_name=job_name, id_a=id_a, id_b=id_b, score=score, explanation=explanation)
        # Populate `why` when we have enough context. Never raises.
        if self._memory_df is not None and self._memory_matchkey_fields:
            try:
                item.why = why_for_correction(
                    id_a, id_b, self._memory_df, self._memory_matchkey_fields,
                    score=score, use_llm=self._use_llm_explainer,
                )
            except Exception as e:  # pragma: no cover - defensive
                log.warning("ReviewQueue why computation failed: %s", e)
        self._backend.add(item)

    def list_pending(self, job_name: str) -> list[ReviewItem]:
        return self._backend.list_pending(job_name)

    def approve(self, job_name: str, id_a: int, id_b: int, decided_by: str) -> None:
        self._backend.approve(job_name, id_a, id_b, decided_by)
        self._record_correction(id_a, id_b, "approve", reason=None)

    def reject(self, job_name: str, id_a: int, id_b: int, decided_by: str, reason: str = "") -> None:
        self._backend.reject(job_name, id_a, id_b, decided_by, reason)
        self._record_correction(id_a, id_b, "reject", reason=reason or None)

    def stats(self, job_name: str) -> dict[str, int]:
        return self._backend.stats(job_name)

    def _record_correction(
        self,
        id_a: int,
        id_b: int,
        decision: str,
        reason: str | None,
    ) -> None:
        """Write a Correction to the optional memory store; never raises."""
        if self._memory_store is None:
            return
        try:
            from goldenmatch.core.memory.corrections import (
                build_row_lookup,
                compute_field_hash,
                compute_record_hash,
            )
            from goldenmatch.core.memory.store import Correction, _canon_pair

            ca, cb = _canon_pair(id_a, id_b)
            field_hash = ""
            record_hash = ""
            if self._memory_df is not None and self._memory_matchkey_fields:
                lookup = build_row_lookup(self._memory_df, self._memory_matchkey_fields)
                if ca in lookup and cb in lookup:
                    field_hash = compute_field_hash(lookup[ca], lookup[cb])
                ra = compute_record_hash(self._memory_df, ca)
                rb = compute_record_hash(self._memory_df, cb)
                if ra and rb:
                    record_hash = f"{ra}:{rb}"

            self._memory_store.add_correction(Correction(
                id=str(uuid.uuid4()),
                id_a=id_a,
                id_b=id_b,
                decision=decision,
                source="steward",
                trust=1.0,
                field_hash=field_hash,
                record_hash=record_hash,
                original_score=0.0,
                matchkey_name=None,
                reason=reason,
                dataset=self._memory_dataset,
                created_at=datetime.now(),
            ))
        except Exception as e:
            log.warning("ReviewQueue memory write failed: %s", e)
