import time
from pathlib import Path
import polars as pl
from goldencheck.engine.scanner import scan_file
from goldencheck.models.finding import Severity

DATASETS_DIR = Path(__file__).parent / "datasets" / "raha_repo" / "datasets"

def compute_ground_truth(dirty_path: Path, clean_path: Path) -> dict:
    """Compare dirty vs clean cell-by-cell to find ground-truth errors."""
    dirty = pl.read_csv(dirty_path, infer_schema_length=0)  # read all as strings
    clean = pl.read_csv(clean_path, infer_schema_length=0)

    # Align columns (use intersection)
    common_cols = [c for c in dirty.columns if c in clean.columns]

    error_cells = set()  # (row_idx, column_name)
    error_columns = set()

    min_rows = min(len(dirty), len(clean))

    for col in common_cols:
        dirty_col = dirty[col].head(min_rows)
        clean_col = clean[col].head(min_rows)
        for i in range(min_rows):
            d_val = dirty_col[i]
            c_val = clean_col[i]
            if str(d_val) != str(c_val):
                error_cells.add((i, col))
                error_columns.add(col)

    return {
        "total_cells": min_rows * len(common_cols),
        "error_cells": len(error_cells),
        "error_columns": error_columns,
        "error_rate": len(error_cells) / (min_rows * len(common_cols)) if min_rows > 0 else 0,
        "rows": min_rows,
        "columns": len(common_cols),
    }

def run_detection_benchmark():
    datasets = ["hospital", "flights", "beers"]
    results = []

    print(f"\n{'='*90}")
    print(f"{'DETECTION BENCHMARK — GoldenCheck vs Ground Truth':^90}")
    print(f"{'='*90}\n")

    for name in datasets:
        dataset_dir = DATASETS_DIR / name
        dirty_path = dataset_dir / "dirty.csv"
        clean_path = dataset_dir / "clean.csv"

        if not dirty_path.exists() or not clean_path.exists():
            print(f"  Skipping {name}: files not found")
            continue

        print(f"--- {name.upper()} ---")

        # Ground truth
        gt = compute_ground_truth(dirty_path, clean_path)
        print(f"  Rows: {gt['rows']:,} | Columns: {gt['columns']}")
        print(f"  Ground truth errors: {gt['error_cells']:,} cells ({gt['error_rate']:.1%})")
        print(f"  Error columns: {gt['error_columns']}")

        # Run GoldenCheck
        start = time.perf_counter()
        findings, profile = scan_file(dirty_path)
        elapsed = time.perf_counter() - start

        errors = [f for f in findings if f.severity == Severity.ERROR]
        warnings = [f for f in findings if f.severity == Severity.WARNING]
        infos = [f for f in findings if f.severity == Severity.INFO]

        # Check which ground-truth error columns were detected
        detected_columns = {f.column for f in findings if f.severity in (Severity.ERROR, Severity.WARNING)}
        gt_detected = gt["error_columns"] & detected_columns
        column_recall = len(gt_detected) / len(gt["error_columns"]) if gt["error_columns"] else 0

        # Total affected rows flagged
        total_affected = sum(f.affected_rows for f in findings if f.severity in (Severity.ERROR, Severity.WARNING))

        print(f"  GoldenCheck findings: {len(errors)} errors, {len(warnings)} warnings, {len(infos)} info")
        print(f"  Time: {elapsed:.2f}s")
        print(f"  Columns with ground-truth errors: {len(gt['error_columns'])}")
        print(f"  Columns detected by GoldenCheck: {len(gt_detected)}/{len(gt['error_columns'])} ({column_recall:.0%})")
        print(f"  Detected: {gt_detected}")
        print(f"  Missed: {gt['error_columns'] - detected_columns}")
        print(f"  Total affected rows flagged: {total_affected}")
        print()

        results.append({
            "dataset": name,
            "rows": gt["rows"],
            "columns": gt["columns"],
            "gt_error_cells": gt["error_cells"],
            "gt_error_columns": len(gt["error_columns"]),
            "findings_total": len(findings),
            "errors": len(errors),
            "warnings": len(warnings),
            "columns_detected": len(gt_detected),
            "column_recall": column_recall,
            "time_s": elapsed,
        })

    # Summary table
    print(f"{'='*90}")
    print(f"{'SUMMARY':^90}")
    print(f"{'='*90}")
    print(f"{'Dataset':<12} {'Rows':<8} {'GT Errors':<12} {'Findings':<10} {'Col Recall':<12} {'Time (s)'}")
    print(f"{'-'*90}")
    for r in results:
        print(f"{r['dataset']:<12} {r['rows']:<8} {r['gt_error_cells']:<12} {r['findings_total']:<10} {r['column_recall']:<12.0%} {r['time_s']:.2f}")
    print(f"{'='*90}")

if __name__ == "__main__":
    run_detection_benchmark()
