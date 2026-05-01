"""Build and validate report.json from case results."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypedDict

import jsonschema


class MetricSet(TypedDict):
    """Aggregated scorecard metrics for a slice of results."""
    f1: float
    top1: float
    mrr: float
    ece: float
    n: int


class Report(TypedDict):
    """Shape of a single-language benchmark report.json."""
    version: int
    language: str
    infermap_version: str
    runner_version: str
    ran_at: str
    duration_seconds: float
    scorecard: dict[str, Any]
    per_case: list[dict[str, Any]]
    failed_cases: list[str]

from . import REPORT_VERSION
from .metrics import (
    Prediction,
    expected_calibration_error,
    macro_mean,
    micro_f1,
)


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    category: str
    subcategory: str
    difficulty: str
    tags: list[str]
    top1: float
    f1: float
    mrr: float
    tp: int
    fp: int
    fn: int
    predictions: list[Prediction]
    failed: bool = False
    failure_reason: str | None = None


def _round(n: float, digits: int = 6) -> float:
    return round(n, digits)


def _metric_set(results: list[CaseResult]) -> dict:
    counts = [(r.tp, r.fp, r.fn) for r in results]
    all_predictions: list[Prediction] = []
    for r in results:
        all_predictions.extend(r.predictions)
    return {
        "f1": _round(micro_f1(counts)),
        "top1": _round(macro_mean([r.top1 for r in results])),
        "mrr": _round(macro_mean([r.mrr for r in results])),
        "ece": _round(expected_calibration_error(all_predictions)),
        "n": len(results),
    }


def _by_single_key(
    results: list[CaseResult],
    key: Callable[[CaseResult], str],
) -> dict:
    buckets: dict[str, list[CaseResult]] = {}
    for r in results:
        buckets.setdefault(key(r), []).append(r)
    return {k: _metric_set(buckets[k]) for k in sorted(buckets)}


def _by_tag(results: list[CaseResult]) -> dict:
    buckets: dict[str, list[CaseResult]] = {}
    for r in results:
        for tag in r.tags:
            buckets.setdefault(tag, []).append(r)
    return {k: _metric_set(buckets[k]) for k in sorted(buckets)}


def build_report(
    results: list[CaseResult],
    *,
    language: str,
    infermap_version: str,
    runner_version: str,
    duration_seconds: float,
) -> Report:
    """Assemble a report.json dict from a list of per-case results.

    Aggregates metrics into overall, by_difficulty, by_category, and by_tag
    slices, and emits one per_case entry per result. The return value conforms
    to the `Report` TypedDict and the committed report.schema.json.
    """
    ran_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "version": REPORT_VERSION,
        "language": language,
        "infermap_version": infermap_version,
        "runner_version": runner_version,
        "ran_at": ran_at,
        "duration_seconds": duration_seconds,
        "scorecard": {
            "overall": _metric_set(results),
            "by_difficulty": _by_single_key(results, lambda r: r.difficulty),
            "by_category": _by_single_key(results, lambda r: r.category),
            "by_tag": _by_tag(results),
        },
        "per_case": [
            {
                "id": r.case_id,
                "f1": _round(r.f1),
                "top1": _round(r.top1),
                "mrr": _round(r.mrr),
                "expected_n": r.tp + r.fn,
                "predicted_n": r.tp + r.fp,
                "true_positives": r.tp,
                "false_positives": r.fp,
                "false_negatives": r.fn,
                "failed": r.failed,
                "failure_reason": r.failure_reason,
            }
            for r in results
        ],
        "failed_cases": [r.case_id for r in results if r.failed],
    }


def validate_report(report: dict, schema_path: Path | str) -> None:
    """Validate a report dict against report.schema.json. Raises on mismatch."""
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    jsonschema.validate(report, schema)


def write_report(
    report: dict, output_path: Path | str, schema_path: Path | str
) -> None:
    """Validate a report and atomically write it as pretty-printed JSON."""
    validate_report(report, schema_path)
    Path(output_path).write_text(
        json.dumps(report, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
