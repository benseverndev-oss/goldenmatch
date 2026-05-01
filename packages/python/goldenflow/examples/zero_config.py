"""Zero-config transform -- GoldenFlow auto-detects and fixes issues.

Usage:
    pip install goldenflow
    python examples/zero_config.py
"""
from __future__ import annotations

import csv
import tempfile
from pathlib import Path

import polars as pl


def create_messy_data() -> Path:
    rows = [
        ["first_name", "last_name", "email", "phone", "city", "amount"],
        ["  John  ", "SMITH", "JOHN@TEST.COM", "(555) 123-4567", "new york", "$1,234.56"],
        ["jane", "  doe  ", "jane@test.com", "5559876543", "  Chicago  ", "999.99"],
        ["BOB", "Wilson", "BOB@TEST.COM", "555.111.2222", "boston", "$50.00"],
        ["Alice", "brown", "alice@test.com", "(555) 333-4444", "AUSTIN", "1,500"],
    ]
    path = Path(tempfile.mktemp(suffix=".csv"))
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    return path


if __name__ == "__main__":
    import goldenflow

    path = create_messy_data()
    print("=" * 60)
    print("GoldenFlow -- Zero-Config Transform")
    print("=" * 60)

    df = pl.read_csv(path)
    print("\nBefore:")
    print(df)

    result = goldenflow.transform_df(df)
    print(f"\nTransformed {len(result.manifest.records)} columns")
    for rec in result.manifest.records:
        print(f"  {rec.column}: {rec.transform} ({rec.affected_rows} rows changed)")

    print("\nAfter:")
    print(result.df)

    path.unlink()
