"""Zero-config transform -- auto-detect column types and apply fixes.

GoldenFlow profiles each column, infers its semantic type, and applies
matching transforms (e.g., phone normalization, date parsing) automatically.

Usage:
    python transform_basic.py data.csv
"""
from pathlib import Path
import sys

from goldenflow import transform_file


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data.csv")
    result = transform_file(path, output_dir=Path("output"))

    print(f"Transforms applied: {len(result.manifest.records)}")
    for rec in result.manifest.records:
        print(f"  {rec.column}: {rec.transform} ({rec.rows_affected} rows changed)")

    print(f"\nOutput written to output/")
    print(f"Result DataFrame: {result.df.shape[0]} rows x {result.df.shape[1]} cols")


if __name__ == "__main__":
    main()
