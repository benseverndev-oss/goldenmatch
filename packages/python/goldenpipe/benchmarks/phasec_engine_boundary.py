#!/usr/bin/env python3
"""Phase C baseline: for engine-resident data, does an in-engine stage beat
pulling the whole table into Python?

Phase C of the relocatable-stage contract is a stage that runs on another engine
(DuckDB / Postgres / a TS worker) instead of in-process Python. Unlike the
in-process handoff (Stage 0: free, 0.2%) and the input-frame streaming (Phase B:
the frame is a rounding error), the ENGINE boundary is where the handoff becomes
real: pulling a table out of DuckDB into Python and pushing the result back is a
full serialization + materialization.

This probe compares, on a DuckDB-resident table, one representative transform
stage (normalize email = ``lower(trim(email))``) run two ways:

  - **pull**  : DuckDB table -> Polars (``.pl()``) -> transform in Polars ->
                write result back into DuckDB. (Today's goldenpipe model: every
                stage materializes the whole table in Python.)
  - **inengine**: ``CREATE TABLE out AS SELECT lower(trim(email)) ... FROM t`` --
                the data never leaves the engine.
  - **crossing**: just the boundary cost -- ``.pl()`` extract + reinsert -- to
                size the handoff a relocated stage's Arrow path would carry.

Run one mode in a FRESH process (peak RSS is a process-lifetime max):
    python benchmarks/phasec_engine_boundary.py --rows 1000000 --mode inengine
"""
from __future__ import annotations

import argparse
import random
import resource
import time

import duckdb
import polars as pl


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KB->MB on Linux


def _seed_table(con: duckdb.DuckDBPyConnection, rows: int, seed: int = 7) -> float:
    """Create table ``t`` of entity rows inside DuckDB. Returns its in-DB size (MB)."""
    rng = random.Random(seed)
    first = ["Jon", "John", "Mary", "Bob", "Robert", "Sue"]
    last = ["Smith", "Smyth", "Jones", "Lee", "Kim", "Patel"]
    recs = []
    for i in range(rows):
        f, ln = rng.choice(first), rng.choice(last)
        recs.append({"id": i, "first": f, "last": ln,
                     "email": f"  {f.upper()}.{ln.upper()}{rng.randint(0, 99)}@X.COM  ",
                     "city": rng.choice(["NYC", "LA", "Chicago"]), "amt": round(rng.random() * 1000, 2)})
    df = pl.DataFrame(recs)
    con.register("seed_df", df)
    con.execute("CREATE TABLE t AS SELECT * FROM seed_df")
    con.unregister("seed_df")
    mb = df.estimated_size() / 1e6
    del df
    return mb


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--mode", choices=["pull", "inengine", "crossing"], default="inengine")
    args = ap.parse_args()

    con = duckdb.connect()
    frame_mb = _seed_table(con, args.rows)

    t0 = time.perf_counter()
    if args.mode == "pull":
        # Stage runs in Python: extract whole table -> transform -> reinsert.
        t_e0 = time.perf_counter()
        df = con.sql("SELECT * FROM t").pl()
        extract = time.perf_counter() - t_e0
        t_t0 = time.perf_counter()
        out = df.with_columns(pl.col("email").str.to_lowercase().str.strip_chars())
        transform = time.perf_counter() - t_t0
        t_r0 = time.perf_counter()
        con.register("out_df", out)
        con.execute("CREATE TABLE out AS SELECT * FROM out_df")
        reinsert = time.perf_counter() - t_r0
        total = time.perf_counter() - t0
        print(f"mode=pull      rows={args.rows:>8}  frame={frame_mb:6.1f}MB  "
              f"total={total * 1000:7.0f}ms  (extract={extract * 1000:.0f} + "
              f"transform={transform * 1000:.0f} + reinsert={reinsert * 1000:.0f})  "
              f"peak_RSS={_peak_rss_mb():6.0f}MB")
    elif args.mode == "inengine":
        # Stage runs in the engine: data never leaves DuckDB.
        con.execute("CREATE TABLE out AS SELECT id, first, last, "
                    "lower(trim(email)) AS email, city, amt FROM t")
        total = time.perf_counter() - t0
        print(f"mode=inengine  rows={args.rows:>8}  frame={frame_mb:6.1f}MB  "
              f"total={total * 1000:7.0f}ms  peak_RSS={_peak_rss_mb():6.0f}MB")
    else:  # crossing
        t_e0 = time.perf_counter()
        df = con.sql("SELECT * FROM t").pl()
        extract = time.perf_counter() - t_e0
        t_r0 = time.perf_counter()
        con.register("out_df", df)
        con.execute("CREATE TABLE out AS SELECT * FROM out_df")
        reinsert = time.perf_counter() - t_r0
        print(f"mode=crossing  rows={args.rows:>8}  frame={frame_mb:6.1f}MB  "
              f"extract={extract * 1000:6.0f}ms  reinsert={reinsert * 1000:6.0f}ms  "
              f"peak_RSS={_peak_rss_mb():6.0f}MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
