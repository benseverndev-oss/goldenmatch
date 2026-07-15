#!/usr/bin/env python
"""Single-datapoint converted-Splink dedupe runner for the ER head-to-head bench.

The `gm_converted_splink` lane: GoldenMatch running SPLINK's own settings, auto-
converted via `goldenmatch.config.from_splink`. Builds the shape's Splink
`SettingsCreator` (from shapes.py -- the single source of truth the `splink` lane
also uses), serializes it with `create_settings_dict(sql_dialect_str="duckdb")`,
converts it, and dedupes the fixture with the converted `GoldenMatchConfig`.

Unlike the dataset-keyed parity gate in `run_converted_splink.py` (from which the
conversion flow is reused), this runner is FIXTURE-keyed (`--input`), shape-aware
(`--shape`), and emits the STANDARD `(shape, lane, scale)` result JSON + a STRING
record_id pred parquet -- the same subprocess contract every other runner obeys
(`--input --rows --out --pred-out --threshold --shape`).

Splink must be installed for this lane (it builds a real `SettingsCreator`); a
missing Splink is a `skipped` result (exit 0), never a crash. A conversion that
yields no usable config is a `refused` result carrying the ConversionReport
summary. Every payload -- ok, skipped, refused, error -- carries `lane` + `shape`.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

try:
    import resource  # Unix-only; absent on Windows dev boxes (CI/bench runs on Linux)
except ImportError:  # pragma: no cover - Windows fallback path
    resource = None

# Must be set BEFORE importing goldenmatch/polars so the loader + planner see them.
os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")  # clean, reproducible CI runs
os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

_LANE = "gm_converted_splink"


def _load_shapes_module():
    """Import the sibling shapes.py whether run as a script or from elsewhere."""
    try:
        import shapes as _shapes  # type: ignore
        return _shapes
    except ImportError:
        pass
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        import shapes as _shapes  # type: ignore
        return _shapes
    except ImportError:
        sh_path = here / "shapes.py"
        spec = importlib.util.spec_from_file_location("shapes", sh_path)
        if spec is None or spec.loader is None:
            raise
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


def _peak_rss_mb() -> float | None:
    # Linux ru_maxrss is in KiB; this is the process high-water mark (load + dedupe).
    if resource is None:  # Windows dev box: no rusage, perf RSS only meaningful on CI.
        return None
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, path)


def _base_result(shape: str, rows: int, threshold: float) -> dict:
    """Every payload -- ok, skipped, refused, error -- carries lane + shape."""
    return {
        "lane": _LANE,
        "engine": "goldenmatch",
        "backend": "converted-splink",
        "shape": shape,
        "rows_requested": rows,
        "threshold": threshold,
        "status": "error",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pred-out", type=Path, default=None,
                    help="write {record_id, pred_cluster_id} parquet for accuracy eval")
    ap.add_argument("--threshold", type=float, default=0.85)
    ap.add_argument("--shape", choices=["person", "biblio"], default="person",
                    help="fixture shape; selects the Splink settings from shapes.py")
    args = ap.parse_args()

    result = _base_result(args.shape, args.rows, args.threshold)
    t_start = time.perf_counter()

    # Splink import: a missing competitor engine is a SKIP, never a crash. The
    # skip payload STILL carries lane + shape (the test asserts them
    # unconditionally, before the ok-guard).
    try:
        import splink  # noqa: F401
        import splink.comparison_library as cl
        from splink import DuckDBAPI, Linker, SettingsCreator, block_on
    except ImportError as e:
        result.update(status="skipped", reason=f"splink not installed: {e}")
        result["total_wall_seconds"] = round(time.perf_counter() - t_start, 2)
        result["peak_rss_mb"] = _peak_rss_mb()
        _atomic_write(args.out, result)
        print(f"[gm_converted] shape={args.shape} status=skipped reason=splink-missing")
        return

    try:
        import polars as pl

        from goldenmatch.config.from_splink import SplinkConversionError, from_splink
        from goldenmatch.core.bench import bench_capture

        try:
            from goldenmatch import dedupe_df
        except ImportError:  # older layouts expose this on _api
            from goldenmatch._api import dedupe_df

        shapes = _load_shapes_module()
        s = {
            "DuckDBAPI": DuckDBAPI,
            "Linker": Linker,
            "SettingsCreator": SettingsCreator,
            "block_on": block_on,
            "cl": cl,
        }

        # Build the SAME Splink settings the `splink` lane runs for this shape, then
        # serialize to the REAL dict the recognizers must parse and convert it.
        settings, _training_rules = shapes.SHAPES[args.shape].splink_settings(s)
        settings_dict = settings.create_settings_dict(sql_dialect_str="duckdb")
        try:
            conversion = from_splink(settings_dict)
        except SplinkConversionError as e:
            result.update(status="refused", conversion_summary=str(e))
            result["total_wall_seconds"] = round(time.perf_counter() - t_start, 2)
            result["peak_rss_mb"] = _peak_rss_mb()
            _atomic_write(args.out, result)
            print(f"[gm_converted] shape={args.shape} status=refused reason=conversion-error")
            return

        summary = conversion.report.summary()
        result["conversion_summary"] = summary
        config = conversion.config
        # A conversion that yields no usable matchkey is a refusal, not a crash.
        if config is None or not config.get_matchkeys():
            result.update(status="refused")
            result["total_wall_seconds"] = round(time.perf_counter() - t_start, 2)
            result["peak_rss_mb"] = _peak_rss_mb()
            _atomic_write(args.out, result)
            print(f"[gm_converted] shape={args.shape} status=refused reason=no-matchkeys")
            return

        # Force rerank off so a weighted matchkey can't pull a cross-encoder model
        # down from HuggingFace at dedupe time.
        for mk in config.get_matchkeys():
            if getattr(mk, "type", None) == "weighted":
                mk.rerank = False

        t0 = time.perf_counter()
        df = pl.read_parquet(args.input)
        result["rows_loaded"] = df.height
        load_wall = time.perf_counter() - t0

        t0 = time.perf_counter()
        with bench_capture() as bench:
            ded = dedupe_df(df, config=config)
        dedupe_wall = time.perf_counter() - t0

        # Pred parquet: remap internal __row_id__ back to the input df's REAL
        # record_id as a STRING column (mirrors run_goldenmatch.py autoconfig branch).
        if args.pred_out is not None:
            import numpy as np
            import pyarrow as pa
            import pyarrow.parquet as pq

            clusters = getattr(ded, "clusters", None) or {}
            rid = df["record_id"].to_list()
            rids, cids = [], []
            for cid, c in clusters.items():
                members = c["members"] if isinstance(c, dict) else c.members
                for m in members:
                    rids.append(str(rid[m]))
                    cids.append(cid)
            pq.write_table(
                pa.table(
                    {
                        "record_id": pa.array(rids, pa.string()),
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
            cluster_count=metrics.get("cluster_count"),
            multi_member_clusters=metrics.get("multi_member_cluster_count"),
            bench=bench_blob,
        )
    except MemoryError as e:
        result.update(status="OOM", error=f"{type(e).__name__}: {e}")
    except BaseException as e:  # noqa: BLE001 - record any failure
        result.update(status="error", error=f"{type(e).__name__}: {e}")
        raise
    finally:
        result["total_wall_seconds"] = round(time.perf_counter() - t_start, 2)
        result["peak_rss_mb"] = _peak_rss_mb()
        _atomic_write(args.out, result)
        print(
            f"[gm_converted] shape={args.shape} rows={args.rows:,} "
            f"status={result['status']} "
            f"dedupe={result.get('dedupe_wall_seconds')}s "
            f"peak_rss={result['peak_rss_mb']}MB pairs={result.get('scored_pairs')}"
        )


if __name__ == "__main__":
    main()
