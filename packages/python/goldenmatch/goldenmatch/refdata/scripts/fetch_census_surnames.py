"""Regenerate ``goldenmatch/refdata/data/census_surnames_2010_top10k.csv``.

Pulls the public-domain U.S. Census 2010 surnames archive, extracts
``Names_2010Census.csv``, filters to ranks 1–10000, drops demographic
columns, and writes the bundled file in place. Run when the upstream
file changes (rare — the 2010 file has been stable since 2016) or when
bumping the bundled top-N cutoff.

Usage::

    python -m goldenmatch.refdata.scripts.fetch_census_surnames

Network required. Idempotent — overwrites the existing bundle.
"""
from __future__ import annotations

import csv
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ARCHIVE_URL = "https://www2.census.gov/topics/genealogy/2010surnames/names.zip"
INNER_FILE = "Names_2010Census.csv"
TOP_N = 10_000


def main() -> int:
    target = (
        Path(__file__).resolve().parents[1] / "data" / "census_surnames_2010_top10k.csv"
    )
    print(f"Fetching {ARCHIVE_URL} ...", file=sys.stderr)
    with urllib.request.urlopen(ARCHIVE_URL, timeout=60) as resp:
        archive_bytes = resp.read()
    print(f"  {len(archive_bytes):,} bytes downloaded", file=sys.stderr)

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as z:
        with z.open(INNER_FILE) as f:
            text = io.TextIOWrapper(f, encoding="utf-8")
            rdr = csv.DictReader(text)
            rows: list[tuple[int, str, int]] = []
            for r in rdr:
                if r["rank"] == "0":  # 'ALL OTHER NAMES' aggregate row
                    continue
                try:
                    rank = int(r["rank"])
                except ValueError:
                    continue
                if rank > TOP_N:
                    continue
                rows.append((rank, r["name"], int(r["count"])))

    rows.sort()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["name", "rank", "count"])
        for rank, name, count in rows:
            w.writerow([name, rank, count])
    print(f"Wrote {target} ({len(rows):,} rows)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
