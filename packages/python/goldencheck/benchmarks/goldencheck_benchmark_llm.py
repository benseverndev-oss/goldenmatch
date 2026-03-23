"""GoldenCheck Benchmark v1 — WITH LLM Boost.

Runs the same benchmark as goldencheck_benchmark.py but uses scan_file_with_llm
to measure the improvement from the LLM enhancement pass.
"""
import json
import time
import sys
from pathlib import Path
from collections import defaultdict

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from goldencheck.engine.scanner import scan_file, scan_file_with_llm
from goldencheck.models.finding import Finding, Severity

BENCH_DIR = Path(__file__).parent / "datasets" / "goldencheck_bench"
DIRTY_PATH = BENCH_DIR / "dirty.csv"
GT_PATH = BENCH_DIR / "ground_truth.json"


def load_ground_truth():
    with open(GT_PATH) as f:
        return json.load(f)


def evaluate(findings: list[Finding], gt: dict) -> dict:
    """Evaluate findings against ground truth."""
    # Build planted column sets per profiler
    planted_by_profiler = {}
    for cat, info in gt["issue_categories"].items():
        planted_by_profiler[cat] = set(info["columns"])

    # Build detected column sets per profiler (WARNING/ERROR only)
    detected_by_profiler = defaultdict(set)
    for f in findings:
        if f.severity in (Severity.ERROR, Severity.WARNING):
            detected_by_profiler[f.check].add(f.column)

    results = {}
    for profiler, planted_cols in planted_by_profiler.items():
        detected_cols = detected_by_profiler.get(profiler, set())
        tp = planted_cols & detected_cols
        fp = detected_cols - planted_cols
        fn = planted_cols - detected_cols

        recall = len(tp) / len(planted_cols) if planted_cols else 0
        precision = len(tp) / (len(tp) + len(fp)) if (len(tp) + len(fp)) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        results[profiler] = {
            "planted": len(planted_cols),
            "detected": len(tp),
            "missed": sorted(fn),
            "recall": recall,
            "precision": precision,
            "f1": f1,
        }

    # Overall — strict (exact check name match)
    all_planted = set()
    all_detected_strict = set()
    for cat, info in gt["issue_categories"].items():
        for col in info["columns"]:
            all_planted.add((cat, col))
    for f in findings:
        if f.severity in (Severity.ERROR, Severity.WARNING):
            key = (f.check, f.column)
            if key in all_planted:
                all_detected_strict.add(key)

    # Overall — column-level (any WARNING/ERROR on a planted column counts)
    planted_columns = set()
    for cat, info in gt["issue_categories"].items():
        for col in info["columns"]:
            planted_columns.add(col)
    # Also check comma-joined column names for cross-column findings
    detected_columns = set()
    for f in findings:
        if f.severity in (Severity.ERROR, Severity.WARNING):
            detected_columns.add(f.column)
            # Split comma-joined columns
            for part in f.column.split(","):
                detected_columns.add(part.strip())

    column_recall = len(planted_columns & detected_columns) / len(planted_columns) if planted_columns else 0
    strict_recall = len(all_detected_strict) / len(all_planted) if all_planted else 0

    return {
        "per_profiler": results,
        "overall_recall": strict_recall,
        "column_recall": column_recall,
        "planted_columns": planted_columns,
        "detected_columns": detected_columns & planted_columns,
        "missed_columns": planted_columns - detected_columns,
    }


def print_results(label: str, findings: list[Finding], eval_results: dict, elapsed: float):
    print(f"\n{'='*80}")
    print(f"  {label}")
    print(f"{'='*80}")

    errors = sum(1 for f in findings if f.severity == Severity.ERROR)
    warnings = sum(1 for f in findings if f.severity == Severity.WARNING)
    infos = len(findings) - errors - warnings
    llm_count = sum(1 for f in findings if f.source == "llm")

    print(f"  Findings: {errors} errors, {warnings} warnings, {infos} info | LLM-sourced: {llm_count}")
    print(f"  Time: {elapsed:.2f}s\n")

    print(f"  {'Profiler':<24} {'Planted':>8} {'Detected':>9} {'Recall':>8} {'Missed'}")
    print(f"  {'-'*75}")

    for profiler, r in eval_results["per_profiler"].items():
        missed_str = ", ".join(r["missed"]) if r["missed"] else "-"
        marker = "" if r["recall"] == 1.0 else " !!" if r["recall"] == 0 else " !"
        print(f"  {profiler:<24} {r['planted']:>8} {r['detected']:>9} {r['recall']:>7.0%}{marker}  {missed_str}")

    print(f"  {'-'*75}")
    print(f"  {'STRICT RECALL':<24} {'':<8} {'':<9} {eval_results['overall_recall']:>7.0%}  (exact check name match)")
    print(f"  {'COLUMN RECALL':<24} {'':<8} {'':<9} {eval_results['column_recall']:>7.0%}  (any WARNING/ERROR on planted column)")
    if eval_results.get("missed_columns"):
        print(f"  Missed columns: {sorted(eval_results['missed_columns'])}")
    print(f"{'='*80}")


def main():
    gt = load_ground_truth()
    print(f"Dataset: {gt['rows']} rows x {gt['columns']} columns | {gt['total_planted_issues']} planted issues\n")

    # --- Profiler-only ---
    print("Running profiler-only scan...")
    t0 = time.perf_counter()
    findings_base, _ = scan_file(DIRTY_PATH)
    t_base = time.perf_counter() - t0
    eval_base = evaluate(findings_base, gt)
    print_results("PROFILER-ONLY (baseline)", findings_base, eval_base, t_base)

    # --- With LLM Boost ---
    print("\nRunning LLM-boosted scan...")
    t0 = time.perf_counter()
    findings_llm, _ = scan_file_with_llm(DIRTY_PATH, provider="openai")
    t_llm = time.perf_counter() - t0
    eval_llm = evaluate(findings_llm, gt)
    print_results("WITH LLM BOOST (OpenAI gpt-4o-mini)", findings_llm, eval_llm, t_llm)

    # --- Comparison ---
    print(f"\n{'='*80}")
    print("  COMPARISON")
    print(f"{'='*80}")
    print(f"  {'Profiler':<24} {'Base Recall':>12} {'LLM Recall':>12} {'Delta':>8}")
    print(f"  {'-'*60}")

    for profiler in eval_base["per_profiler"]:
        base_r = eval_base["per_profiler"][profiler]["recall"]
        llm_r = eval_llm["per_profiler"].get(profiler, {"recall": base_r})["recall"]
        delta = llm_r - base_r
        delta_str = f"+{delta:.0%}" if delta > 0 else f"{delta:.0%}" if delta < 0 else "="
        print(f"  {profiler:<24} {base_r:>11.0%} {llm_r:>11.0%} {delta_str:>8}")

    print(f"  {'-'*60}")
    base_overall = eval_base["overall_recall"]
    llm_overall = eval_llm["overall_recall"]
    delta_overall = llm_overall - base_overall
    delta_str = f"+{delta_overall:.0%}" if delta_overall > 0 else f"{delta_overall:.0%}"
    print(f"  {'OVERALL (strict)':<24} {base_overall:>11.0%} {llm_overall:>11.0%} {delta_str:>8}")

    base_col = eval_base.get("column_recall", 0)
    llm_col = eval_llm.get("column_recall", 0)
    delta_col = llm_col - base_col
    delta_col_str = f"+{delta_col:.0%}" if delta_col > 0 else f"{delta_col:.0%}" if delta_col < 0 else "="
    print(f"  {'OVERALL (column)':<24} {base_col:>11.0%} {llm_col:>11.0%} {delta_col_str:>8}")
    print(f"{'='*80}")

    if eval_base.get("missed_columns") != eval_llm.get("missed_columns"):
        base_missed = eval_base.get("missed_columns", set())
        llm_missed = eval_llm.get("missed_columns", set())
        newly_detected = base_missed - llm_missed
        if newly_detected:
            print(f"\n  COLUMNS NOW DETECTED BY LLM BOOST: {sorted(newly_detected)}")

    # Show what the LLM caught that profilers missed
    llm_findings = [f for f in findings_llm if f.source == "llm"]
    if llm_findings:
        print(f"\n  LLM-SOURCED FINDINGS ({len(llm_findings)} total):")
        for f in llm_findings:
            print(f"    [{f.severity.name}] {f.column}: {f.check} — {f.message[:70]}")


if __name__ == "__main__":
    main()
