"""Scan a CSV file with GoldenCheck and print all findings.

Usage:
    python scan_basic.py data.csv
"""
from pathlib import Path
import sys

from goldencheck import scan_file, Severity


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data.csv")
    findings, profile = scan_file(path)

    print(f"Scanned {profile.row_count} rows, {profile.column_count} columns")
    print(f"Found {len(findings)} issues:\n")

    for f in findings:
        icon = {Severity.ERROR: "ERR", Severity.WARNING: "WRN", Severity.INFO: "INF"}
        print(f"  [{icon[f.severity]}] {f.column}: {f.message} (confidence={f.confidence:.2f})")


if __name__ == "__main__":
    main()
