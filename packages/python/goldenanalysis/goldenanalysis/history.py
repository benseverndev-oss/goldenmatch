"""``ReportHistory`` — an append-only store of ``AnalysisReport``s for cross-run
trend + regression detection.

Mirrors goldenmatch's ``IdentityStore`` persistence idiom (``backend=`` / ``path=``
/ ``connection=`` constructor; a ``SCHEMA_VERSION``) but defaults to **JSONL** (an
append-only reports log is the natural fit) with **SQLite** optional. Both are
stdlib — no new dependencies. Keyed by ``(analysis_name, dataset, run_id)``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from goldenanalysis._regressions import evaluate_metric
from goldenanalysis.models import (
    AnalysisReport,
    Regression,
    RegressionPolicy,
    TrendSeries,
)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis_reports (
    report_id TEXT PRIMARY KEY,
    analysis_name TEXT NOT NULL,
    dataset TEXT NOT NULL,
    run_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    recorded_at TEXT NOT NULL,
    seq INTEGER,
    payload TEXT NOT NULL,
    UNIQUE(analysis_name, dataset, run_id)
);
CREATE INDEX IF NOT EXISTS idx_reports_lookup ON analysis_reports(analysis_name, dataset);
"""


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


class ReportHistory:
    """Append-only ``AnalysisReport`` store with trend + regression queries."""

    def __init__(
        self,
        backend: str = "jsonl",
        path: str | Path = ".golden/analysis.jsonl",
        connection: str | None = None,
        database: str = "goldenanalysis",
    ) -> None:
        self._backend = backend
        self._path = Path(path)
        if backend == "jsonl":
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = None
        elif backend == "sqlite":
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._path), isolation_level=None)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        else:
            raise NotImplementedError(f"Backend {backend!r} not supported")

    # --- write ----------------------------------------------------------

    def append(self, report: AnalysisReport, *, analysis_name: str = "default") -> None:
        """Record a report. Re-appending the same (analysis_name, dataset, run_id)
        replaces the prior one (idempotent upsert)."""
        dataset = report.source.get("dataset", "frame")
        record = {
            "analysis_name": analysis_name,
            "dataset": dataset,
            "run_id": report.run_id,
            "schema_version": report.schema_version,
            "recorded_at": datetime.now(UTC).isoformat(),
            "report": report.model_dump(mode="json"),
        }
        if self._backend == "jsonl":
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        else:
            assert self._conn is not None  # sqlite backend
            report_id = f"{analysis_name}:{dataset}:{report.run_id}"
            self._conn.execute(
                "INSERT OR REPLACE INTO analysis_reports "
                "(report_id, analysis_name, dataset, run_id, schema_version, recorded_at, seq, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, "
                "  COALESCE((SELECT seq FROM analysis_reports WHERE report_id = ?), "
                "           (SELECT COALESCE(MAX(seq), 0) + 1 FROM analysis_reports)), ?)",
                (
                    report_id,
                    analysis_name,
                    dataset,
                    report.run_id,
                    report.schema_version,
                    record["recorded_at"],
                    report_id,
                    json.dumps(record["report"]),
                ),
            )

    # --- read -----------------------------------------------------------

    def reports(self, dataset: str, *, analysis_name: str = "default") -> list[AnalysisReport]:
        """Reports for ``(analysis_name, dataset)`` in insertion order (last-wins
        per run_id)."""
        if self._backend == "jsonl":
            latest: dict[tuple[str, str, str], dict] = {}
            order: dict[tuple[str, str, str], int] = {}
            if self._path.exists():
                for i, line in enumerate(self._path.read_text(encoding="utf-8").splitlines()):
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    key = (rec["analysis_name"], rec["dataset"], rec["run_id"])
                    if key not in order:
                        order[key] = i
                    latest[key] = rec
            picked = [
                (order[k], rec)
                for k, rec in latest.items()
                if k[0] == analysis_name and k[1] == dataset
            ]
            picked.sort(key=lambda t: t[0])
            return [AnalysisReport.model_validate(rec["report"]) for _, rec in picked]
        assert self._conn is not None  # sqlite backend
        rows = self._conn.execute(
            "SELECT payload FROM analysis_reports WHERE analysis_name = ? AND dataset = ? ORDER BY seq",
            (analysis_name, dataset),
        ).fetchall()
        return [AnalysisReport.model_validate(json.loads(r[0])) for r in rows]

    def trend(
        self, metric_key: str, dataset: str, *, last_n: int = 30, analysis_name: str = "default"
    ) -> TrendSeries:
        """A metric's value across the run history (oldest -> newest)."""
        points: list[tuple[str, float]] = []
        for rep in self.reports(dataset, analysis_name=analysis_name):
            value = next((_as_float(m.value) for m in rep.metrics if m.key == metric_key), None)
            if value is not None:
                points.append((rep.run_id, value))
        return TrendSeries(metric_key=metric_key, dataset=dataset, points=points[-last_n:])

    def detect_regressions(
        self,
        dataset: str,
        *,
        baseline: str = "rolling_median",
        window: int = 7,
        policy: RegressionPolicy | None = None,
        analysis_name: str = "default",
    ) -> list[Regression]:
        """Flag metric movements in the LATEST report vs the prior history.

        Compares each numeric metric in the most-recent report against a baseline
        derived from the earlier reports under the chosen strategy + per-metric
        policy. Returns only the flagged regressions.
        """
        policy = policy or RegressionPolicy()
        history = self.reports(dataset, analysis_name=analysis_name)
        if len(history) < 2:
            return []
        *prior, current = history
        out: list[Regression] = []
        for metric in current.metrics:
            value = _as_float(metric.value)
            if value is None:
                continue
            series = _prior_series(prior, metric.key)
            if not series:
                continue
            reg = evaluate_metric(
                key=metric.key,
                direction=metric.direction,
                history=series,
                current=value,
                strategy=baseline,
                window=window,
                policy=policy,
            )
            if reg is not None and reg.flagged:
                out.append(reg)
        return out


def _prior_series(prior: Sequence[AnalysisReport], metric_key: str) -> list[float]:
    values: list[float] = []
    for rep in prior:
        v = next((_as_float(m.value) for m in rep.metrics if m.key == metric_key), None)
        if v is not None:
            values.append(v)
    return values
