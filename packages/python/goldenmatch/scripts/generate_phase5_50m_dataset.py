"""One-shot wrapper to produce bench_50000000.parquet for the simulated
cluster bench. Calls scripts/generate_phase5_dataset.py with rows=50M.

Ops note: ~8 hr single-node generation cost on a `large-new-64GB`
runner. Run once, upload as a release asset to bench-dataset-v1.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    out = Path("bench-dataset/bench_50000000.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"generating 50M rows -> {out}")
    script = Path(__file__).resolve().parent / "generate_phase5_dataset.py"
    return subprocess.call(
        [
            sys.executable,
            str(script),
            "--rows",
            "50000000",
            "--out",
            str(out),
        ],
    )


if __name__ == "__main__":
    raise SystemExit(main())
