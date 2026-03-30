"""Configure specific transforms per column.

Usage:
    pip install goldenflow
    python examples/configured_transform.py
"""
from __future__ import annotations

import csv
import tempfile
from pathlib import Path

import polars as pl


def create_messy_data() -> Path:
    rows = [
        ["first_name", "last_name", "email", "phone", "city"],
        ["  john  ", "SMITH", "JOHN@ACME.COM", "(555) 123-4567", "new york"],
        ["JANE", "  doe  ", "jane@corp.com", "555.987.6543", "  chicago  "],
    ]
    path = Path(tempfile.mktemp(suffix=".csv"))
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    return path


if __name__ == "__main__":
    import goldenflow
    from goldenflow.config.schema import GoldenFlowConfig, TransformSpec

    path = create_messy_data()
    df = pl.read_csv(path)

    config = GoldenFlowConfig(transforms=[
        TransformSpec(column="first_name", ops=["strip", "title_case"]),
        TransformSpec(column="last_name", ops=["strip", "title_case"]),
        TransformSpec(column="email", ops=["strip", "lowercase"]),
        TransformSpec(column="phone", ops=["strip", "phone_national"]),
        TransformSpec(column="city", ops=["strip", "title_case"]),
    ])

    print("Before:")
    print(df)

    result = goldenflow.transform_df(df, config=config)
    print("\nAfter (configured transforms):")
    print(result.df)

    print(f"\nManifest: {len(result.manifest.records)} transforms applied")
    path.unlink()
