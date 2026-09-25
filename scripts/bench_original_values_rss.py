#!/usr/bin/env python3
"""Peak RSS of a file dedupe with and without kept original values (#3003).

Keeping the pre-standardization input costs memory for every column that
standardization rewrites; this measures what that is on a realistic file, to set
`output_tables.ORIGINAL_VALUES_MAX_ROWS`.

Each arm runs in a FRESH process (a same-process A/B shares allocator state and
hash seeds), reports peak RSS from `resource.getrusage` (Linux; ru_maxrss is KB)
and wall time, and prints one JSON line.

    python scripts/bench_original_values_rss.py DATA.csv            # both arms
    python scripts/bench_original_values_rss.py DATA.csv --arm keep # one arm
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time


def _run_arm(path: str, keep: bool) -> dict:
    import resource

    import goldenmatch as gm

    t0 = time.perf_counter()
    result = gm.dedupe(path, keep_original_values=keep)
    wall = time.perf_counter() - t0
    return {
        "arm": "keep" if keep else "standardized",
        "rows": result.total_records,
        "entities": result.total_entities,
        "wall_s": round(wall, 2),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--arm", choices=["keep", "standardized"])
    args = ap.parse_args()
    if args.arm:
        print(json.dumps(_run_arm(args.path, args.arm == "keep")), flush=True)
        return 0
    for arm in ("standardized", "keep"):
        out = subprocess.run(
            [sys.executable, __file__, args.path, "--arm", arm],
            capture_output=True,
            text=True,
            check=False,
        )
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        print(
            lines[-1] if lines else json.dumps({"arm": arm, "error": out.stderr[-800:]}), flush=True
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
