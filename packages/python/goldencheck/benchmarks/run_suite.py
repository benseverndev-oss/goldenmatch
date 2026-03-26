"""Formal benchmark suite for GoldenCheck.

Runs speed and detection benchmarks on all CSV files in benchmarks/datasets/,
prints a summary table, and saves results to benchmarks/results.json.
"""
from __future__ import annotations

import glob
import json
import time
from pathlib import Path

from goldencheck import scan_file, apply_confidence_downgrade
from goldencheck.models.finding import Finding, Severity


def _findings_by_column(findings: list[Finding]) -> dict[str, dict[str, int]]:
    """Build findings-by-column dict for health_score (same as MCP server)."""
    by_col: dict[str, dict[str, int]] = {}
    for f in findings:
        if f.severity >= Severity.WARNING:
            by_col.setdefault(f.column, {"errors": 0, "warnings": 0})
            key = "errors" if f.severity == Severity.ERROR else "warnings"
            by_col[f.column][key] = by_col[f.column].get(key, 0) + 1
    return by_col


def run_speed_benchmark(
    files: list[Path],
) -> dict[str, dict[str, int | float]]:
    """Time scan_file on each CSV.

    Returns {name: {rows, columns, findings, seconds, rows_per_sec}}.
    """
    results: dict[str, dict[str, int | float]] = {}
    for file in files:
        name = file.stem
        start = time.perf_counter()
        findings, profile = scan_file(file)
        elapsed = time.perf_counter() - start
        rows_per_sec = profile.row_count / elapsed if elapsed > 0 else 0
        results[name] = {
            "rows": profile.row_count,
            "columns": profile.column_count,
            "findings": len(findings),
            "seconds": round(elapsed, 4),
            "rows_per_sec": round(rows_per_sec, 1),
        }
    return results


def run_detection_benchmark(
    files: list[Path],
) -> dict[str, dict[str, str | int]]:
    """Count findings by severity, compute health grade.

    Returns {name: {grade, score, errors, warnings, info, total}}.
    """
    results: dict[str, dict[str, str | int]] = {}
    for file in files:
        name = file.stem
        findings, profile = scan_file(file)
        findings = apply_confidence_downgrade(findings, llm_boost=False)

        errors = sum(1 for f in findings if f.severity == Severity.ERROR)
        warnings = sum(1 for f in findings if f.severity == Severity.WARNING)
        info = sum(1 for f in findings if f.severity == Severity.INFO)

        fbc = _findings_by_column(findings)
        grade, score = profile.health_score(findings_by_column=fbc)

        results[name] = {
            "grade": grade,
            "score": score,
            "errors": errors,
            "warnings": warnings,
            "info": info,
            "total": len(findings),
        }
    return results


def _print_speed_table(results: dict[str, dict]) -> None:
    """Print speed benchmark results as a formatted table."""
    header = f"{'Dataset':<30} {'Rows':>8} {'Cols':>6} {'Finds':>7} {'Secs':>8} {'Rows/s':>10}"
    print("\n=== Speed Benchmark ===")
    print(header)
    print("-" * len(header))
    for name, data in results.items():
        print(
            f"{name:<30} {data['rows']:>8} {data['columns']:>6} "
            f"{data['findings']:>7} {data['seconds']:>8.4f} "
            f"{data['rows_per_sec']:>10.1f}"
        )


def _print_detection_table(results: dict[str, dict]) -> None:
    """Print detection benchmark results as a formatted table."""
    header = (
        f"{'Dataset':<30} {'Grade':>6} {'Score':>6} "
        f"{'Errors':>7} {'Warns':>7} {'Info':>6} {'Total':>7}"
    )
    print("\n=== Detection Benchmark ===")
    print(header)
    print("-" * len(header))
    for name, data in results.items():
        print(
            f"{name:<30} {data['grade']:>6} {data['score']:>6} "
            f"{data['errors']:>7} {data['warnings']:>7} "
            f"{data['info']:>6} {data['total']:>7}"
        )


def main() -> None:
    """Glob benchmarks/datasets/*.csv, run both benchmarks, print and save."""
    suite_dir = Path(__file__).resolve().parent
    pattern = str(suite_dir / "datasets" / "**" / "*.csv")
    csv_files = sorted(Path(p) for p in glob.glob(pattern, recursive=True))

    if not csv_files:
        print("No CSV files found in benchmarks/datasets/")
        return

    print(f"Found {len(csv_files)} dataset(s)")

    speed_results = run_speed_benchmark(csv_files)
    _print_speed_table(speed_results)

    detection_results = run_detection_benchmark(csv_files)
    _print_detection_table(detection_results)

    output = {
        "speed": speed_results,
        "detection": detection_results,
    }
    output_path = suite_dir / "results.json"
    output_path.write_text(json.dumps(output, indent=2))
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
