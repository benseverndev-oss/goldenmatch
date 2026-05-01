"""
GoldenCheck custom benchmark — compares scanner findings against a planted ground truth.

Run:
    python benchmarks/goldencheck_benchmark.py
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from goldencheck.engine.scanner import scan_file

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BENCH_DIR = Path(__file__).parent / "datasets" / "goldencheck_bench"
CSV_PATH = BENCH_DIR / "dirty.csv"
GT_PATH = BENCH_DIR / "ground_truth.json"

# ---------------------------------------------------------------------------
# Mapping: ground-truth check name → Finding.check value emitted by scanner
# ---------------------------------------------------------------------------
# The scanner uses these check names in Finding.check:
#   type_inference, nullability, uniqueness, format_detection,
#   range_distribution, cardinality, pattern_consistency,
#   temporal_order, null_correlation
#
# These happen to match the ground-truth keys directly.
GT_CHECK_TO_PROFILER = {
    "type_inference": "type_inference",
    "nullability": "nullability",
    "uniqueness": "uniqueness",
    "format_detection": "format_detection",
    "range_distribution": "range_distribution",
    "cardinality": "cardinality",
    "pattern_consistency": "pattern_consistency",
    "temporal_order": "temporal_order",
    "null_correlation": "null_correlation",
}

# Profilers that work on column pairs / groups — Finding.column is "col_a,col_b"
RELATION_PROFILERS = {"temporal_order", "null_correlation"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_ground_truth() -> dict[str, Any]:
    return json.loads(GT_PATH.read_text())


def _profiler_key(profiler_name: str, column: str) -> str:
    """Canonical (profiler, column) key used for lookup."""
    return f"{profiler_name}::{column}"


def _columns_for_finding(finding_column: str, profiler: str) -> list[str]:
    """
    For relation profilers the Finding.column is 'col_a,col_b'.
    Return individual column names.
    """
    if profiler in RELATION_PROFILERS:
        return [c.strip() for c in finding_column.split(",")]
    return [finding_column]


def _f1(precision: float, recall: float) -> float:
    denom = precision + recall
    if denom == 0:
        return 0.0
    return 2 * precision * recall / denom


# ---------------------------------------------------------------------------
# Core evaluation logic
# ---------------------------------------------------------------------------

def _build_planted_index(gt: dict[str, Any]) -> dict[str, list[dict]]:
    """
    Returns a dict keyed by profiler name → list of planted-issue entries
    that that profiler is responsible for.
    """
    index: dict[str, list[dict]] = {p: [] for p in GT_CHECK_TO_PROFILER}
    for issue in gt["planted_issues"]:
        profiler = GT_CHECK_TO_PROFILER.get(issue["check"])
        if profiler:
            index[profiler].append(issue)
    return index


def _categorise_findings(
    findings: list[Any],
) -> dict[str, dict[str, list[Any]]]:
    """
    Returns { profiler_name: { column: [finding, ...] } }
    """
    categorised: dict[str, dict[str, list[Any]]] = {}
    for f in findings:
        profiler = f.check  # Finding.check == profiler name
        if profiler not in categorised:
            categorised[profiler] = {}
        cols = _columns_for_finding(f.column, profiler)
        for col in cols:
            categorised[profiler].setdefault(col, []).append(f)
    return categorised


def evaluate_profiler(
    profiler_name: str,
    planted: list[dict],
    categorised_findings: dict[str, dict[str, list[Any]]],
    gt: dict[str, Any],
) -> dict[str, Any]:
    """Compute recall/precision/F1 for one profiler at column level."""

    # Columns that *should* have been flagged (from planted ground truth)
    planted_cols: set[str] = set()
    for issue in planted:
        planted_cols.add(issue["column"])

    # Columns that the scanner actually flagged with this profiler
    detected_cols: set[str] = set(categorised_findings.get(profiler_name, {}).keys())

    # For relation profilers, expand planted column names
    # (temporal_order: "last_login" → we need to check if temporal_order fired
    #  on any pair that includes those columns)
    if profiler_name == "temporal_order":
        # Planted columns: signup_date, last_login (as a pair)
        planted_cols = {"signup_date", "last_login"}
    if profiler_name == "null_correlation":
        planted_cols = {"shipping_address", "shipping_city", "shipping_zip"}

    true_positives = planted_cols & detected_cols
    false_negatives = planted_cols - detected_cols
    false_positives = detected_cols - planted_cols

    n_planted = len(planted_cols)
    n_detected = len(detected_cols)
    n_tp = len(true_positives)

    recall = n_tp / n_planted if n_planted > 0 else 0.0
    precision = n_tp / n_detected if n_detected > 0 else 0.0
    f1 = _f1(precision, recall)

    # Row-level recall: of planted affected_rows, how many were captured?
    # GoldenCheck findings don't enumerate row indices, so we use affected_rows count
    # as a proxy and compare against Finding.affected_rows sums.
    planted_affected_count = sum(i["planted_count"] for i in planted)
    detected_affected_count = sum(
        f.affected_rows
        for col_findings in categorised_findings.get(profiler_name, {}).values()
        for f in col_findings
        if f.affected_rows > 0
    )
    row_recall = (
        min(detected_affected_count, planted_affected_count) / planted_affected_count
        if planted_affected_count > 0
        else None
    )

    return {
        "profiler": profiler_name,
        "planted_cols": sorted(planted_cols),
        "detected_cols": sorted(detected_cols),
        "n_planted": n_planted,
        "n_detected": n_detected,
        "n_tp": n_tp,
        "recall": recall,
        "precision": precision,
        "f1": f1,
        "planted_affected_count": planted_affected_count,
        "detected_affected_count": detected_affected_count,
        "row_recall": row_recall,
        "false_negatives": sorted(false_negatives),
        "false_positives": sorted(false_positives),
    }


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def _pct(v: float | None) -> str:
    if v is None:
        return " N/A"
    return f"{v * 100:6.1f}%"


def _label(n: int, singular: str = "col", plural: str | None = None) -> str:
    if plural is None:
        plural = singular + "s"
    return f"{n} {singular if n == 1 else plural}"


def print_report(
    results: list[dict],
    gt: dict[str, Any],
    n_findings: int,
    elapsed: float,
    categorised_findings: dict[str, dict[str, list[Any]]] | None = None,
) -> None:
    total_planted = gt["total_planted_issues"]
    n_rows = gt["rows"]
    n_cols = gt["columns"]
    n_categories = len(gt["issue_categories"])

    print()
    print("=" * 80)
    print(f"{'GOLDENCHECK BENCHMARK v1 — RESULTS':^80}")
    print("=" * 80)
    print()
    print(
        f"Dataset: {n_rows:,} rows x {n_cols} columns | "
        f"{n_categories} issue categories | {total_planted} total planted issues"
    )
    print(
        f"Scanner: {n_findings} findings in {elapsed:.2f}s"
    )
    print()

    # --- per-profiler table ---
    header = f"{'Profiler':<22} {'Planted':>9} {'Detected':>9} {'Recall':>8} {'Precision':>10} {'F1':>8} {'RowRecall':>10}"
    print("PER-PROFILER RESULTS:")
    print("-" * 80)
    print(header)
    print("-" * 80)

    overall_planted_cols: set[str] = set()
    overall_detected_cols: set[str] = set()
    overall_tp_cols: set[str] = set()

    for r in results:
        planted_label = _label(r["n_planted"])
        detected_label = _label(r["n_detected"])
        recall_str = _pct(r["recall"])
        precision_str = _pct(r["precision"])
        f1_str = _pct(r["f1"])
        row_recall_str = _pct(r["row_recall"])

        flag = ""
        if r["recall"] < 1.0:
            flag = " !"
        if r["recall"] == 0.0:
            flag = " !!"

        print(
            f"{r['profiler']:<22} {planted_label:>9} {detected_label:>9} "
            f"{recall_str} {precision_str} {f1_str} {row_recall_str}{flag}"
        )
        overall_planted_cols.update(r["planted_cols"])
        overall_detected_cols.update(r["detected_cols"])
        overall_tp_cols.update(set(r["planted_cols"]) & set(r["detected_cols"]))

    print("-" * 80)
    # Overall
    n_op = len(overall_planted_cols)
    n_od = len(overall_detected_cols)
    n_tp = len(overall_tp_cols)
    overall_recall = n_tp / n_op if n_op > 0 else 0.0
    overall_precision = n_tp / n_od if n_od > 0 else 0.0
    overall_f1 = _f1(overall_precision, overall_recall)
    print(
        f"{'OVERALL':<22} {_label(n_op):>9} {_label(n_od):>9} "
        f"{_pct(overall_recall)} {_pct(overall_precision)} {_pct(overall_f1)} {'':>10}"
    )
    print("=" * 80)

    # --- detail: missed & unexpected ---
    misses = [r for r in results if r["false_negatives"]]
    unexpected = [r for r in results if r["false_positives"]]

    # Known architectural limitations — explain WHY certain issues are not detected
    _KNOWN_LIMITS: dict[str, str] = {
        "type_inference::last_name": (
            "TypeInferenceProfiler requires >=80% numeric values; only 3/5000 (0.06%) "
            "last_name values are numeric — below threshold by design."
        ),
        "type_inference::age": (
            "age is read as String due to mixed word strings; TypeInferenceProfiler would "
            "flag it but RangeDistributionProfiler cannot see the numeric outliers."
        ),
        "range_distribution::age": (
            "age column is String dtype (mixed words+numbers); RangeDistributionProfiler "
            "only runs on numeric dtypes — numeric outliers are invisible until re-cast."
        ),
        "pattern_consistency::country": (
            "Country codes are 2-letter uppercase strings; invalid codes like 'XX'/'ZZ' "
            "share the same LL pattern as valid codes — pattern profiler cannot distinguish."
        ),
        "temporal_order::signup_date": (
            "TemporalOrderProfiler matches column-name keywords (start/end, created/updated). "
            "'signup_date'/'last_login' do not match any keyword — extend _PAIR_HEURISTICS "
            "to add ('signup', 'last_login') or ('signup', 'login')."
        ),
        "temporal_order::last_login": (
            "Same as signup_date — temporal pair not discovered by keyword heuristic."
        ),
    }

    if misses:
        print()
        print("MISSED COLUMNS (planted issues NOT detected):")
        for r in misses:
            for col in r["false_negatives"]:
                note = _KNOWN_LIMITS.get(f"{r['profiler']}::{col}", "")
                note_str = f"\n      NOTE: {note}" if note else ""
                print(f"  [{r['profiler']}] {col}{note_str}")

    if unexpected:
        print()
        print("UNEXPECTED DETECTIONS (columns flagged but no planted issues):")
        print("  (INFO-level findings on all columns are expected — profilers are verbose)")
        for r in unexpected:
            # Filter to only WARNING/ERROR unexpected detections for signal
            high_sev = [
                col for col in r["false_positives"]
                if categorised_findings and any(
                    f.severity >= 2  # WARNING or ERROR
                    for f in categorised_findings.get(r["profiler"], {}).get(col, [])
                )
            ]
            if high_sev:
                print(f"  [{r['profiler']}] WARNING/ERROR on: {', '.join(high_sev)}")

    print()
    print("ISSUE CATEGORY BREAKDOWN:")
    print("-" * 60)
    for cat, info in gt["issue_categories"].items():
        cols = ", ".join(info["columns"])
        print(f"  {cat:<24} planted={info['total_planted']:>4}  cols=[{cols}]")
    print("=" * 80)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_benchmark() -> None:
    import time

    # Generate dataset if it doesn't exist
    if not CSV_PATH.exists() or not GT_PATH.exists():
        print("Dataset not found — generating…")
        from benchmarks.generate_datasets import generate_goldencheck_bench
        generate_goldencheck_bench()

    print(f"Loading ground truth from {GT_PATH}…")
    gt = _load_ground_truth()

    print(f"Running GoldenCheck scanner on {CSV_PATH}…")
    t0 = time.perf_counter()
    findings, profile = scan_file(CSV_PATH)
    elapsed = time.perf_counter() - t0
    print(f"Scanner returned {len(findings)} findings in {elapsed:.2f}s")

    # Index scanner findings
    categorised = _categorise_findings(findings)

    # Build planted index
    planted_index = _build_planted_index(gt)

    # Evaluate each profiler
    results = []
    for profiler_name in GT_CHECK_TO_PROFILER:
        r = evaluate_profiler(
            profiler_name,
            planted_index[profiler_name],
            categorised,
            gt,
        )
        results.append(r)

    print_report(results, gt, len(findings), elapsed, categorised_findings=categorised)


if __name__ == "__main__":
    run_benchmark()
