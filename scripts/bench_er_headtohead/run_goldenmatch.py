#!/usr/bin/env python
"""Single-datapoint GoldenMatch dedupe runner for the ER head-to-head bench.

Runs ONE (engine=goldenmatch, rows=N) measurement in its own process, so all of
its memory is reclaimed by the OS on exit. Writes one atomic JSON result and exits.

Most-optimized path: bucket backend + native compiled runtime + native Arrow
block-scorer. We set GOLDENMATCH_NATIVE=1 so a missing/unbuilt native runtime
raises instead of silently falling back to pure Python — a silent fallback would
make the comparison a lie. Verified again via native_enabled() before timing.
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import time
from pathlib import Path

# Must be set BEFORE importing goldenmatch so the native loader + planner see them.
os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")  # clean, reproducible CI runs
os.environ.setdefault("GOLDENMATCH_PLANNER_BUCKET", "1")  # prefer bucket scorer
# GOLDENMATCH_NATIVE is set from --require-native below, before the heavy imports.


def _peak_rss_mb() -> float:
    # Linux ru_maxrss is in KiB; this is the process high-water mark (load + dedupe).
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pred-out", type=Path, default=None,
                    help="write {record_id, pred_cluster_id} parquet for accuracy eval")
    ap.add_argument("--threshold", type=float, default=0.85)
    ap.add_argument("--require-native", action="store_true", default=True)
    ap.add_argument("--allow-pure-python", dest="require_native", action="store_false")
    args = ap.parse_args()

    os.environ["GOLDENMATCH_NATIVE"] = "1" if args.require_native else "auto"

    result: dict = {
        "engine": "goldenmatch",
        "backend": "bucket+native+arrow",
        "rows_requested": args.rows,
        "status": "error",
        "threshold": args.threshold,
    }
    t_start = time.perf_counter()
    try:
        import polars as pl

        from goldenmatch.core._native_loader import native_enabled, native_module
        from goldenmatch.core.bench import bench_capture

        try:
            from goldenmatch import auto_configure_df, dedupe_df
        except ImportError:  # older layouts expose these on _api
            from goldenmatch._api import auto_configure_df, dedupe_df

        native_loaded = native_module() is not None
        result["native_loaded"] = native_loaded
        result["native_block_scoring"] = bool(native_enabled("block_scoring"))
        if args.require_native and not (native_loaded and native_enabled("block_scoring")):
            raise RuntimeError(
                "Native Arrow block-scorer is NOT active; refusing to report a "
                "pure-Python number as the optimized backend. Build it with "
                "`python scripts/build_native.py` or install goldenmatch[native]."
            )

        t0 = time.perf_counter()
        df = pl.read_parquet(args.input)
        result["rows_loaded"] = df.height
        load_wall = time.perf_counter() - t0

        # GoldenMatch's realistic optimized path: zero-config controller, then pin
        # bucket + disable the cross-encoder rerank (avoids a HuggingFace download).
        config = auto_configure_df(df, confidence_required=False)
        config.backend = "bucket"
        if hasattr(config, "rerank"):
            config.rerank = False
        for mk in getattr(config, "get_matchkeys", lambda: [])():
            if getattr(mk, "rerank", None) is not None:
                mk.rerank = False
            if getattr(mk, "threshold", None) is not None:
                mk.threshold = args.threshold

        t0 = time.perf_counter()
        with bench_capture() as bench:
            ded = dedupe_df(df, config=config)
        dedupe_wall = time.perf_counter() - t0

        # Per-record cluster assignment for accuracy eval. clusters is
        # {cid: {"members": [__row_id__...]}} over ALL records; the fixture's
        # record_id IS the input row index, and GoldenMatch preserves it as
        # __row_id__, so member row-ids are record-ids directly.
        if args.pred_out is not None:
            import numpy as np
            import pyarrow as pa
            import pyarrow.parquet as pq

            clusters = getattr(ded, "clusters", None) or {}
            rids, cids = [], []
            for cid, c in clusters.items():
                members = c["members"] if isinstance(c, dict) else c.members
                rids.extend(members)
                cids.extend([cid] * len(members))
            pq.write_table(
                pa.table(
                    {
                        "record_id": pa.array(np.asarray(rids, dtype=np.int64)),
                        "pred_cluster_id": pa.array(np.asarray(cids, dtype=np.int64)),
                    }
                ),
                args.pred_out,
                compression="zstd",
            )

        bench_blob = bench.to_dict()
        metrics = bench_blob.get("metrics", {}) if isinstance(bench_blob, dict) else {}

        result.update(
            status="ok",
            load_wall_seconds=round(load_wall, 2),
            dedupe_wall_seconds=round(dedupe_wall, 2),
            scored_pairs=metrics.get("scored_pair_count"),
            block_count=metrics.get("block_count_scored") or metrics.get("block_count"),
            # cluster_count = total resolved entities incl. singletons, to match
            # Splink's `count(distinct cluster_id)`. multi-member tracked separately.
            cluster_count=metrics.get("cluster_count"),
            multi_member_clusters=metrics.get("multi_member_cluster_count"),
            duplicate_rows_found=getattr(getattr(ded, "dupes", None), "height", None),
            unique_records=getattr(getattr(ded, "unique", None), "height", None),
            bench=bench_blob,
        )
    except MemoryError as e:
        result.update(status="OOM", error=f"{type(e).__name__}: {e}")
    except BaseException as e:  # noqa: BLE001 - record any failure, including SystemError
        result.update(status="error", error=f"{type(e).__name__}: {e}")
        raise
    finally:
        result["total_wall_seconds"] = round(time.perf_counter() - t_start, 2)
        result["peak_rss_mb"] = _peak_rss_mb()
        _atomic_write(args.out, result)
        print(
            f"[goldenmatch] rows={args.rows:,} status={result['status']} "
            f"dedupe={result.get('dedupe_wall_seconds')}s "
            f"peak_rss={result['peak_rss_mb']}MB pairs={result.get('scored_pairs')}"
        )


if __name__ == "__main__":
    main()
