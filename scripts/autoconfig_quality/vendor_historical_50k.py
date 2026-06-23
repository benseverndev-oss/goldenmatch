"""One-time generator: pull splink's historical_50k and vendor it as a committed
parquet so the harness reads a fixed, version-independent source (CI == local).
Run locally with splink installed:  python -m scripts.autoconfig_quality.vendor_historical_50k
The parquet keeps the `cluster` truth column; the harness loader drops it before dedupe.
"""
from __future__ import annotations

from pathlib import Path

import polars as pl

_OUT = Path(__file__).resolve().parent / "vendored" / "historical_50k.parquet"


def main() -> None:
    from splink import splink_datasets
    df = pl.from_pandas(splink_datasets.historical_50k)
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(_OUT)
    print(f"wrote {_OUT} ({df.height} rows, cols={df.columns})")


if __name__ == "__main__":
    main()
