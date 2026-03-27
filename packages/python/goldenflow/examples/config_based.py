"""Transform a CSV using a YAML config for explicit control.

The config specifies exactly which transforms to apply to which columns,
plus renames, drops, and dedup settings.

Usage:
    python config_based.py data.csv goldenflow.yaml
"""
from pathlib import Path
import sys

from goldenflow import transform_file, load_config


def main():
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data.csv")
    config_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("goldenflow.yaml")

    config = load_config(config_path)
    result = transform_file(csv_path, config=config, output_dir=Path("output"))

    print(f"Config source: {config_path}")
    print(f"Transforms applied: {len(result.manifest.records)}")
    for rec in result.manifest.records:
        print(f"  {rec.column}: {rec.transform} ({rec.rows_affected} rows)")

    if result.manifest.errors:
        print(f"\nErrors: {len(result.manifest.errors)}")
        for err in result.manifest.errors:
            print(f"  {err.column}: {err.transform} - {err.message}")


if __name__ == "__main__":
    main()
