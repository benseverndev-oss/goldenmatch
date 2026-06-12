#!/usr/bin/env python3
"""Railway one-shot entrypoint for the #510 quality-invariant scale ladder.

Reads:
  QIS_ROWS    rows for this rung (default 1_000_000)
  QIS_SHAPE   realistic | phase5 (default realistic)
  QIS_SEED    seed (default 0)
  QIS_BACKEND polars | bucket | chunked | duckdb | ray (default: planner auto-pick)
              At >=10M on a default Railway container the planner can land on
              `polars` and OOM. Recommended: duckdb 10M+, ray 50M+.
  QIS_CORRUPTION light | moderate | hard (default light). realistic-shape only;
              the published #510 ladder uses `moderate`.

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
    backend = os.environ.get("QIS_BACKEND", "").strip()
    corruption = os.environ.get("QIS_CORRUPTION", "light").strip() or "light"
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    print(f"=== QIS rung: rows={rows} shape={shape} seed={seed} "
          f"backend={backend or 'auto'} corruption={corruption} ===", flush=True)
    cmd = [sys.executable, "scripts/quality_invariant_scale.py",
           "--rows", rows, "--shape", shape, "--seed", seed,
           "--corruption", corruption]
    if backend:
        cmd += ["--backend", backend]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
