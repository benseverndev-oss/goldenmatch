"""Evaluate scan results against expected findings (ground truth)."""
from __future__ import annotations

from goldencheck.models.finding import Finding


def evaluate_scan(
    findings: list[Finding],
    expected: list[dict],
) -> dict:
    """Compare scan findings against ground-truth expected findings.

    Args:
        findings: Actual findings from a scan.
        expected: List of dicts with "column" and "check" keys.

    Returns:
        Dict with precision, recall, f1, counts, and detail tuples.
    """
    actual_keys = {(f.column, f.check) for f in findings}
    expected_keys = {(e["column"], e["check"]) for e in expected}

    tp = actual_keys & expected_keys
    fp = actual_keys - expected_keys
    fn = expected_keys - actual_keys

    precision = len(tp) / (len(tp) + len(fp)) if (len(tp) + len(fp)) > 0 else 1.0
    recall = len(tp) / (len(tp) + len(fn)) if (len(tp) + len(fn)) > 0 else 1.0
    f1 = (
        2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    )

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "true_positives": len(tp),
        "false_positives": len(fp),
        "false_negatives": len(fn),
        "tp_details": sorted(tp),
        "fp_details": sorted(fp),
        "fn_details": sorted(fn),
    }
