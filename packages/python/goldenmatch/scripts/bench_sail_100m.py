"""S4 binding bench DRIVER (scaffold). Connects to a REAL Sail cluster via
SAIL_REMOTE, runs run_sail_pipeline over a parquet at scale, times it, writes
JSON. No in-process server -- needs a real BYO cluster (not run in this plan).
Usage: SAIL_REMOTE=sc://host:port python bench_sail_100m.py --input <parquet>."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="parquet path/URI on the cluster")
    ap.add_argument("--id-col", default="__row_id__")
    ap.add_argument("--block-col", default="last_name_soundex")
    ap.add_argument("--value-col", default="last_name")
    ap.add_argument("--golden-cols", default="first_name,email")
    ap.add_argument("--out", default=".profile_tmp/sail_100m.json")
    ap.add_argument(
        "--wcc-checkpoint-interval",
        type=int,
        default=2,
        help="truncate the scale-WCC pointer-jump lineage every N rounds "
        "(0=off); the 100M lineage-growth guard. Needs --wcc-checkpoint-dir.",
    )
    ap.add_argument(
        "--wcc-checkpoint-dir",
        default=None,
        help="writable path/URI reachable by the cluster (e.g. gs://.../wcc-ckpt) "
        "for the WCC lineage barrier. Defaults to <input dir>/_wcc_ckpt.",
    )
    args = ap.parse_args()

    remote = os.environ.get("SAIL_REMOTE")
    if not remote:
        print(
            "::error::SAIL_REMOTE unset -- this bench needs a real BYO Sail cluster.",
            file=sys.stderr,
        )
        return 2

    from goldenmatch.sail.pipeline import run_sail_pipeline
    from goldenmatch.sail.session import connect

    spark = connect(remote)
    src = spark.read.parquet(args.input)
    # Default the WCC checkpoint dir next to the input so the 100M run gets the
    # lineage barrier without extra flags (override with --wcc-checkpoint-dir).
    ckpt_dir = args.wcc_checkpoint_dir
    if args.wcc_checkpoint_interval and not ckpt_dir:
        import posixpath

        ckpt_dir = posixpath.join(posixpath.dirname(args.input.rstrip("/")), "_wcc_ckpt")
    t0 = time.perf_counter()
    golden = run_sail_pipeline(
        src,
        id_col=args.id_col,
        block_col=args.block_col,
        value_col=args.value_col,
        golden_cols=args.golden_cols.split(","),
        wcc="scale",
        wcc_checkpoint_interval=args.wcc_checkpoint_interval,
        wcc_checkpoint_dir=ckpt_dir,
    )
    n_golden = golden.count()  # forces the full pipeline
    wall = time.perf_counter() - t0

    payload = {
        "wall_s": wall,
        "golden_count": n_golden,
        "remote": remote,
        "input": args.input,
        "wcc_checkpoint_interval": args.wcc_checkpoint_interval,
        "wcc_checkpoint_dir": ckpt_dir,
    }
    print(json.dumps(payload, indent=2))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
