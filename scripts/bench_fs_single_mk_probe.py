#!/usr/bin/env python
"""Controlled single-FS-matchkey scale probe — isolates B2c (the FS
columnar-cluster path) from the zero-config confounds.

The zero-config path at 5M emits a MULTI-matchkey config (exact cols + a
probabilistic one), which B2c's single-matchkey eligibility gate excludes, so
`bench-zero-config` can't cleanly measure B2c. This probe pins a FIXED
single-probabilistic-matchkey config on the `bucket` route — the exact shape
B2c is eligible for — so `GOLDENMATCH_FS_COLUMNAR_CLUSTER` 1-vs-0 is a real A/B.

Runs one `dedupe_df` under `bench_capture()` with a VmRSS sampler thread and
dumps per-stage {wall, peak RSS} (reading the recorder's real keys,
`stage_timings_seconds` / `stage_peak_rss_kb`). Linux only (ru_maxrss / /proc).

Usage:
    python scripts/bench_fs_single_mk_probe.py <fixture.csv> [--block last_name]
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

# env BEFORE importing goldenmatch (native loader reads these)
os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")
os.environ.setdefault("GOLDENMATCH_NATIVE", "1")
os.environ.setdefault("GOLDENMATCH_FS_NATIVE", "1")

import resource  # noqa: E402


def _vmrss_mb() -> float:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024.0
    return 0.0


class Sampler(threading.Thread):
    def __init__(self, interval: float = 0.05) -> None:
        super().__init__(daemon=True)
        self.interval = interval
        self.peak = 0.0
        self._stopev = threading.Event()

    def run(self) -> None:
        while not self._stopev.is_set():
            self.peak = max(self.peak, _vmrss_mb())
            time.sleep(self.interval)

    def halt(self) -> None:
        self._stopev.set()
        self.join(timeout=1)


def _single_mk_config(block_field: str):
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="fs", type="probabilistic", fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3,
                          partial_threshold=0.85),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=3,
                          partial_threshold=0.85),
            MatchkeyField(field="email", scorer="exact", levels=2),
        ])],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=[block_field])]),
        backend="bucket",  # the in-memory FS bucket route B2c is eligible for
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture", type=Path)
    ap.add_argument("--block", default="last_name",
                    help="blocking field (default last_name — the generator tunes its block sizes)")
    ap.add_argument("--ingest", choices=("polars", "arrow"), default="polars",
                    help="Frame the fixture is fed to dedupe_df as. 'arrow' (pa.Table) "
                         "removes the caller-injected polars so the internal lane stays "
                         "arrow until a genuine polars island forces a bridge; 'polars' "
                         "(pl.read_csv, the historical default) keeps prior runs comparable.")
    args = ap.parse_args()

    from goldenmatch.core._native_loader import native_enabled
    from goldenmatch.core.bench import bench_capture

    try:
        from goldenmatch import dedupe_df
    except ImportError:
        from goldenmatch._api import dedupe_df

    _drop_cols = ("id", "cluster_id")
    if args.ingest == "arrow":
        # Read every column AS TEXT (matches pl.read_csv(infer_schema_length=0):
        # no dtype inference, leading zeros preserved) straight into a pa.Table, so
        # NO polars touches the ingest. column_types needs the names up front.
        import csv as _csv

        import pyarrow as pa
        import pyarrow.csv as pacsv

        with open(args.fixture, newline="") as _fh:
            _names = next(_csv.reader(_fh))
        # Match pl.read_csv semantics: an EMPTY cell is MISSING (null), not the
        # literal string "". pyarrow otherwise reads "" as a real value, which the
        # FS scorer treats as a disagreement instead of unobserved -- the whole
        # source of the earlier arrow-vs-polars blocking/cluster gap (a reader
        # bug, NOT a goldenmatch lane difference; the engine is already arrow-native).
        _convert = pacsv.ConvertOptions(
            column_types={c: pa.string() for c in _names},
            strings_can_be_null=True,
            null_values=[""],
        )
        _parse = pacsv.ParseOptions(invalid_row_handler=lambda _row: "skip")
        df = pacsv.read_csv(args.fixture, parse_options=_parse, convert_options=_convert)
        _keep = [c for c in df.column_names if c not in _drop_cols]
        df = df.select(_keep)
        n = df.num_rows
    else:
        import polars as pl
        df = pl.read_csv(args.fixture, ignore_errors=True, infer_schema_length=0)
        for drop in _drop_cols:
            if drop in df.columns:
                df = df.drop(drop)
        n = df.height
    rss_after_load = _vmrss_mb()
    cfg = _single_mk_config(args.block)

    sampler = Sampler()
    sampler.start()
    t0 = time.perf_counter()
    with bench_capture() as bench:
        res = dedupe_df(df, config=cfg)
    wall = time.perf_counter() - t0
    sampler.halt()

    ru_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    bd = bench.to_dict() if hasattr(bench, "to_dict") else {}
    timings = bd.get("stage_timings_seconds") or {}
    peaks_kb = bd.get("stage_peak_rss_kb") or {}
    metrics = bd.get("metrics") or {}

    columnar = os.environ.get("GOLDENMATCH_FS_COLUMNAR_CLUSTER", "(unset->default)")
    n_clusters = len(res.clusters or {}) if hasattr(res, "clusters") else "?"

    print(f"=== single-FS-mk probe  N={n:,}  block={args.block} ===")
    print(f"GOLDENMATCH_FS_COLUMNAR_CLUSTER={columnar}  block_scoring_native={native_enabled('block_scoring')}")
    print(f"rss_after_load_mb={rss_after_load:.0f}")
    print(f"dedupe_wall_s={wall:.2f}  clusters={n_clusters}")
    print(f"peak_rss_sampled_mb={sampler.peak:.0f}  ru_maxrss_mb={ru_peak:.0f}")
    print(f"peak_over_baseload_mb={sampler.peak - rss_after_load:.0f}")
    # Print the headline metrics first (stable order), then any others the
    # pipeline recorded (e.g. golden_fused_used / golden_quality_scores_len for
    # the golden-stage instrumentation) so new diagnostics surface without a
    # whitelist edit.
    _headline = ("record_count", "scored_pair_count", "cluster_count", "block_count")
    for k in _headline:
        if k in metrics:
            print(f"metric {k}={metrics[k]}")
    for k in sorted(metrics):
        if k not in _headline:
            print(f"metric {k}={metrics[k]}")
    print(f"\n{'stage':36s} {'wall_s':>9s} {'peak_rss_mb':>12s}")
    for k in sorted(set(timings) | set(peaks_kb), key=lambda k: -(peaks_kb.get(k, 0))):
        t = timings.get(k)
        p = peaks_kb.get(k, 0) / 1024.0
        ts = f"{t:.2f}" if isinstance(t, (int, float)) else "-"
        print(f"  {k:34s} {ts:>9s} {p:>12.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
