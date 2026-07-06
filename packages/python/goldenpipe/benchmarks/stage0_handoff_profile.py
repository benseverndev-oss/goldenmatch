#!/usr/bin/env python3
"""Stage 0 of the GoldenPipe orchestrator pivot: where does the wall actually go?

Measures a real ``scan -> transform -> dedupe`` pipeline and splits the wall into
**per-stage compute** vs the **handoff / re-materialization** costs that a
streaming Arrow data plane (pillar 2) would remove — so the decision to build a
Rust streaming executor is driven by data, not the thesis diagram.

The two concrete handoff costs in today's design (see the adapters):
  1. ``goldencheck.scan`` takes the source *path* and RE-READS/RE-PARSES the CSV,
     even though the pipeline already holds the data in ``ctx.df`` — the data is
     loaded twice.
  2. ``goldenmatch.dedupe`` does ``ctx.df.cast({col: Utf8})`` — a full-column
     materialization at the match boundary.

A shared Arrow buffer across stages would eliminate both. If they (plus the
between-stage orchestration gap) are a small fraction of the wall, a streaming
executor optimizes a non-bottleneck and pillar 2 should target out-of-core /
cross-process instead.

Run:
    python benchmarks/stage0_handoff_profile.py --rows 20000
"""
from __future__ import annotations

import argparse
import os
import random
import statistics
import tempfile
import time

import polars as pl

_FIRST = ["Jon", "John", "Jonathan", "Mary", "Maria", "Bob", "Robert", "Sue", "Susan", "Bill"]
_LAST = ["Smith", "Smyth", "Jones", "Brown", "Browne", "Lee", "Garcia", "Nguyen", "Patel", "Kim"]
_DOMAINS = ["example.com", "test.org", "mail.net", "corp.co"]
_CITIES = ["New York", "new york", "NYC", "Los Angeles", "LA", "Chicago", "Boston"]


def _make_dataset(path: str, rows: int, seed: int = 7) -> None:
    """A dirty entity table: name/email/city with typos, casing noise, and
    ~20% near-duplicate rows — the shape an ER pipeline is built for."""
    rng = random.Random(seed)
    recs = []
    for i in range(rows):
        f = rng.choice(_FIRST)
        last = rng.choice(_LAST)
        city = rng.choice(_CITIES)
        email = f"{f.lower()}.{last.lower()}{rng.randint(0, 99)}@{rng.choice(_DOMAINS)}"
        recs.append({"id": i, "first": f, "last": last, "email": email, "city": city,
                     "amount": round(rng.random() * 1000, 2)})
        # ~20% near-dup: same person, noisy re-entry
        if rng.random() < 0.2:
            recs.append({"id": rows + i, "first": f, "last": last,
                         "email": f"  {email.upper()} ", "city": city.upper(),
                         "amount": round(rng.random() * 1000, 2)})
    df = pl.DataFrame(recs)
    df.write_csv(path)


def _median_wall(fn, runs: int = 3) -> float:
    return statistics.median(_time(fn) for _ in range(runs))


def _time(fn) -> float:
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=20_000)
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "entities.csv")
    _make_dataset(path, args.rows)
    on_disk = os.path.getsize(path)
    print(f"GoldenPipe Stage-0 handoff profile  (rows≈{args.rows:,}, csv={on_disk/1e6:.1f} MB)\n")

    from goldenpipe import Pipeline

    # --- the pipeline wall + per-stage compute (ctx.timing) ---
    timings: dict[str, float] = {}
    walls: list[float] = []
    for _ in range(args.runs):
        pipe = Pipeline()  # auto-config: scan -> transform -> dedupe
        t0 = time.perf_counter()
        res = pipe.run(source=path)
        walls.append(time.perf_counter() - t0)
        for k, v in (res.timing or {}).items():
            timings.setdefault(k, []).append(v)
    wall = statistics.median(walls)
    stage_med = {k: statistics.median(v) for k, v in timings.items()}
    stage_sum = sum(stage_med.values())

    print(f"  status: {res.status}  |  stages: {list(stage_med)}\n")
    print(f"  {'TOTAL wall':<34}: {wall*1000:9.1f} ms")
    for name, ms in stage_med.items():
        print(f"    stage  {name:<27}: {ms*1000:9.1f} ms  ({ms/wall*100:4.1f}%)")
    gap = wall - stage_sum
    print(f"  {'orchestration gap (plan/route/ctx)':<34}: {gap*1000:9.1f} ms  ({gap/wall*100:4.1f}%)\n")

    # --- isolate the two handoff / re-materialization costs ---
    print("  Handoff / re-materialization costs a shared Arrow buffer would remove:")
    # (1) the CSV re-read goldencheck.scan does (pipeline already loaded ctx.df)
    reread = _median_wall(lambda: pl.read_csv(path, ignore_errors=True, encoding="utf8-lossy"),
                          args.runs)
    print(f"    (1) CSV re-read in scan (path, not ctx.df)  : {reread*1000:9.1f} ms  "
          f"({reread/wall*100:4.1f}% of wall)")
    # (2) the full-df Utf8 cast dedupe does at the match boundary
    df = pl.read_csv(path, ignore_errors=True, encoding="utf8-lossy")
    cast = _median_wall(lambda: df.cast({c: pl.Utf8 for c in df.columns}), args.runs)
    print(f"    (2) full-df Utf8 cast at dedupe boundary    : {cast*1000:9.1f} ms  "
          f"({cast/wall*100:4.1f}% of wall)")
    handoff = reread + cast
    print(f"    {'sum of these handoff costs':<41}: {handoff*1000:9.1f} ms  "
          f"({handoff/wall*100:4.1f}% of wall)\n")

    print("  Read: if per-stage COMPUTE dominates and the handoff costs are a small")
    print("  slice, a streaming Arrow executor optimizes a non-bottleneck — pillar 2")
    print("  should target out-of-core / cross-process pipelines instead.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
