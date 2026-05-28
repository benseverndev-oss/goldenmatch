#!/usr/bin/env python3
"""Railway one-shot entrypoint for the #510 quality-invariant scale ladder.

Reads:
  QIS_ROWS    rows for this rung (default 1_000_000)
  QIS_SHAPE   realistic | phase5 (default realistic)
  QIS_SEED    seed (default 0)

Shells the harness; output (incl. the per-rung JSON) lands in the Railway
deploy logs and is appended to the published table by hand on the dev box.
"""
from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    rows = os.environ.get("QIS_ROWS", "1000000")
    shape = os.environ.get("QIS_SHAPE", "realistic")
    seed = os.environ.get("QIS_SEED", "0")
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    print(f"=== QIS rung: rows={rows} shape={shape} seed={seed} ===", flush=True)
    return subprocess.call(
        [sys.executable, "scripts/quality_invariant_scale.py",
         "--rows", rows, "--shape", shape, "--seed", seed],
    )


if __name__ == "__main__":
    raise SystemExit(main())
