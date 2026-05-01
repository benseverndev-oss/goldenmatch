"""01 — 30-second quickstart.

Smallest possible Golden Suite program. Reads a CSV, deduplicates, writes
golden records.

Run:
    pip install goldenmatch
    python 01_quickstart_dedupe.py customers.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

import goldenmatch as gm


def main(path: str) -> None:
    csv = Path(path)
    if not csv.is_file():
        raise SystemExit(f"file not found: {csv}")

    # Zero-config — auto-detect columns, pick scorers, run.
    result = gm.dedupe(str(csv))

    print(result)  # DedupeResult(records=N, clusters=M, match_rate=X%)
    out = csv.with_name(csv.stem + ".deduped.csv")
    if result.golden is not None:
        result.golden.write_csv(out)
        print(f"wrote golden records → {out}")
    else:
        print("no clusters found (data may be already unique)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python 01_quickstart_dedupe.py path/to/customers.csv")
    main(sys.argv[1])
