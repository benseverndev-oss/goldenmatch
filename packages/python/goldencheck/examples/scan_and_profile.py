"""Scan a CSV for data quality issues.

Usage:
    pip install goldencheck
    python examples/scan_and_profile.py
"""
from __future__ import annotations
import csv
import tempfile
from pathlib import Path

def create_sample_data() -> Path:
    rows = [
        ["id", "name", "email", "age", "signup_date", "status"],
        ["1", "John Smith", "john@test.com", "32", "2024-01-15", "active"],
        ["2", "  Jane Doe  ", "JANE@TEST.COM", "28", "2024-01-15", "active"],
        ["3", "Bob Wilson", "invalid-email", "-5", "15/01/2024", "actve"],
        ["1", "John Smith", "john@test.com", "32", "2024-01-15", "active"],
        ["4", "", "alice@test.com", "999", "2024-13-45", "pending"],
        ["5", "Charlie Davis", "", "forty", "2024-02-01", "ACTIVE"],
        ["6", "Eve Brown", "eve@test.com", "25", "2024-03-01", ""],
    ]
    path = Path(tempfile.mktemp(suffix=".csv"))
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    return path

if __name__ == "__main__":
    import goldencheck

    path = create_sample_data()
    print("=" * 60)
    print("GoldenCheck — Scan & Profile Demo")
    print("=" * 60)

    print("\n── Scan ──")
    findings = goldencheck.scan_file(str(path))
    print(f"Found {len(findings)} issues:")
    for f in findings:
        print(f"  [{f.severity}] {f.column}: {f.check} — {f.message}")

    print("\n── Health Score ──")
    score = goldencheck.health_score(str(path))
    print(f"Health: {score}")

    path.unlink()
