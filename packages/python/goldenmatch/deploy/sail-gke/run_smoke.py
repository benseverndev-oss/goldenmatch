"""In-cluster Sail smoke runner. Connects to the Sail driver over Spark
Connect, runs the full distributed pipeline over the baked-in parquet, and
prints a single ``RESULT {...}`` JSON line plus a connectivity pre-check.

Env:
  SAIL_REMOTE  Spark Connect URL (default: the in-cluster driver Service).
  SMOKE_DATA   parquet dir (default: /data/smoke, baked into the image).
"""
from __future__ import annotations

import json
import os
import sys
import time


def main() -> int:
    remote = os.environ.get(
        "SAIL_REMOTE", "sc://sail-spark-server.sail.svc.cluster.local:50051"
    )
    data = os.environ.get("SMOKE_DATA", "/data/smoke")
    # WCC: "label_prop" converges in ~graph-diameter rounds (few stages for
    # small clusters -> fast under Sail's per-stage worker provisioning);
    # "scale" is pointer-jumping (fixed ~log2(N) rounds, more stages).
    wcc = os.environ.get("SMOKE_WCC", "label_prop")

    from goldenmatch.sail.pipeline import run_sail_pipeline
    from goldenmatch.sail.session import connect

    print(f"[smoke] connecting to {remote}", flush=True)
    spark = connect(remote)

    # Connectivity pre-check: a trivial distributed count.
    try:
        n3 = spark.createDataFrame([(1,), (2,), (3,)], ["x"]).count()
        print(f"[smoke] connectivity_ok rows={n3}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[smoke] connectivity_check_failed: {exc!r}", flush=True)

    src = spark.read.parquet(data)
    n_in = src.count()
    print(f"[smoke] input_rows={n_in}", flush=True)

    t0 = time.perf_counter()
    golden = run_sail_pipeline(
        src,
        id_col="__row_id__",
        block_col="last_name_soundex",
        value_col="last_name",
        golden_cols=["first_name", "email"],
        wcc=wcc,
    )
    n_golden = golden.count()  # forces the full distributed pipeline
    wall = time.perf_counter() - t0

    payload = {
        "wall_s": round(wall, 2),
        "golden_count": n_golden,
        "input_rows": n_in,
        "wcc": wcc,
        "remote": remote,
        "data": data,
    }
    print("RESULT " + json.dumps(payload), flush=True)
    try:
        golden.show(5, truncate=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[smoke] golden.show failed (non-fatal): {exc!r}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
