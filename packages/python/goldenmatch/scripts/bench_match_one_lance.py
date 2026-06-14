#!/usr/bin/env python3
"""Spike: can a Lance-backed base store serve incremental/match_one candidate
retrieval from DISK, removing the in-RAM-base constraint, fast enough to matter?

Follow-up to the batch bake-off (bench_lance_vs_parquet.py), whose verdict was:
Lance loses on batch dedup (eventually full-scans) but wins big on SPARSE
one-shot gathers (6-27x, ~8x less memory). The one path that is purely sparse
one-shot gathers is incremental / streaming / match_one: `core/match_one.py`
queries an ANN index for top-K candidates per probe, then scores them. Today
that path holds the WHOLE base in memory (`rows[faiss_idx]`), so it is RAM-bound.

This bench models the per-probe candidate gather against a large SKEWED base
(realistic Zipfian block sizes, NOT the uniform 3-record synthetic that flatters
sparsity) across three base stores:

  * memory  -- the status quo: load the full base into a polars frame, gather
               candidates in-RAM. Fast per probe, but RSS ~ the whole base.
  * parquet -- base on disk. ANN gather has no random access -> read+gather per
               probe (O(N) each). block gather via sorted predicate pushdown.
  * lance   -- base on disk. ANN gather via `take(ids)` (reads only covering
               pages); block gather via a BTREE scalar index scan.

Two access shapes, the two `match_one` candidate sources:
  * ann   -- top_k scattered row-ids per probe (the FAISS path).
  * block -- all rows sharing the probe's block_key (the exact-blocking path).

Two regimes:
  * stream     -- one probe at a time (true match_one latency; per-call overhead).
  * microbatch -- B probes' candidates gathered in one call (the streaming
                  micro-batch path).

Reports per-probe median latency + peak RSS (VmHWM, isolated per store in a
spawned child -- ru_maxrss inherits the parent high-water mark and cannot
isolate). The headline question: is lance per-probe latency close to `memory`
while keeping RSS near zero?

Usage:
    python scripts/bench_match_one_lance.py --rows 5_000_000 --probes 50 --top-k 50
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import resource
import statistics
import sys
import tempfile
import time
from pathlib import Path

SCORE_COLS = ["id", "block_key", "name", "address", "age", "score_hint"]


def _have(mod: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(mod) is not None


def _peak_rss_mb() -> float:
    """Per-process peak RSS in MB via VmHWM (ru_maxrss inherits parent peak)."""
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / 1024.0 / 1024.0 if sys.platform == "darwin" else raw / 1024.0


def generate_skewed(rows: int, seed: int = 7):
    """Base with a Zipfian block-size distribution (heavy tail, like real
    surname/zip skew) -- the realism the prior synthetic 3-record data lacked."""
    import numpy as np
    import polars as pl

    rng = np.random.default_rng(seed)
    # Zipf draw, clipped, then mod into a key space ~ rows/avg_block_size.
    n_keys = max(1, rows // 20)
    raw = rng.zipf(1.5, size=rows) % n_keys
    block_key = raw.astype(np.int64)
    first = rng.integers(0, 5000, size=rows)
    df = pl.DataFrame(
        {
            "id": np.arange(rows, dtype=np.int64),
            "block_key": [f"{z:08d}" for z in block_key],
            "name": [f"person_{a}_{b}" for a, b in zip(first, rng.integers(0, 5000, size=rows))],
            "address": [f"{s} main st apt {a}" for s, a in zip(rng.integers(0, 9999, size=rows), rng.integers(0, 999, size=rows))],
            "age": rng.integers(18, 95, size=rows).astype("int32"),
            "score_hint": rng.random(size=rows).astype("float32"),
        }
    )
    return df.sort("block_key")


def write_stores(df, root: Path) -> dict[str, Path]:
    import pyarrow as pa  # noqa: F401

    paths: dict[str, Path] = {}
    pq = root / "base.parquet"
    df.write_parquet(pq, row_group_size=128 * 1024, statistics=True)
    paths["parquet"] = pq
    print(f"  parquet: {pq.stat().st_size / 1e6:8.1f} MB")

    if _have("lance"):
        import lance
        import pyarrow as pa

        lpath = root / "base.lance"
        tbl = df.to_arrow()
        if pa.types.is_large_string(tbl.schema.field("block_key").type):
            ci = tbl.schema.get_field_index("block_key")
            tbl = tbl.set_column(ci, "block_key", tbl.column("block_key").cast(pa.string()))
        lance.write_dataset(tbl, str(lpath))
        ds = lance.dataset(str(lpath))
        t0 = time.perf_counter()
        ds.create_scalar_index("block_key", "BTREE")
        size = sum(f.stat().st_size for f in lpath.rglob("*") if f.is_file())
        print(f"  lance  : {size / 1e6:8.1f} MB  (+BTREE idx {time.perf_counter() - t0:.1f}s)")
        paths["lance"] = lpath
    return paths


def _child(q, store, path_str, shape, regime, probe_ids, probe_keys, top_k, n):
    """One store x shape x regime, in an isolated process. Reports per-probe
    median latency, total wall, and peak RSS."""
    import numpy as np

    path = Path(path_str)
    rng = np.random.default_rng(123)

    # ---- per-store retrieval primitives ----
    if store == "memory":
        import polars as pl

        base = pl.read_parquet(path, columns=SCORE_COLS)  # held in RAM
        key_col = base["block_key"]

        def gather_ann(ids):
            return base[ids].height

        def gather_block(key):
            return base.filter(key_col == key).height

    elif store == "parquet":
        import polars as pl

        def gather_ann(ids):
            # no random access: read scoring cols then gather
            return pl.read_parquet(path, columns=SCORE_COLS)[ids].height

        def gather_block(key):
            return pl.scan_parquet(path).filter(pl.col("block_key") == key).select(SCORE_COLS).collect().height

    else:  # lance
        import lance

        ds = lance.dataset(str(path))

        def gather_ann(ids):
            return ds.take(list(ids), columns=SCORE_COLS).num_rows

        def gather_block(key):
            return ds.scanner(columns=SCORE_COLS, filter=f"block_key = '{key}'").to_table().num_rows

    gather = gather_ann if shape == "ann" else gather_block
    # Build the per-probe arguments.
    if shape == "ann":
        args = [np.sort(rng.choice(n, size=top_k, replace=False)) for _ in range(len(probe_ids))]
    else:
        args = list(probe_keys)

    walls = []
    t_all = time.perf_counter()
    if regime == "stream":
        for a in args:
            t0 = time.perf_counter()
            gather(a)
            walls.append(time.perf_counter() - t0)
    else:  # microbatch: one gather of all candidates
        if shape == "ann":
            allids = np.unique(np.concatenate(args))
            t0 = time.perf_counter()
            gather(allids)
            walls.append(time.perf_counter() - t0)
        else:
            for a in args:  # block microbatch == union of predicates; approximate per-key
                t0 = time.perf_counter()
                gather(a)
                walls.append(time.perf_counter() - t0)
    total = time.perf_counter() - t_all
    q.put((statistics.median(walls), total, _peak_rss_mb()))


def measure(store, path, shape, regime, probe_ids, probe_keys, top_k, n):
    ctx = mp.get_context("spawn")
    qq = ctx.Queue()
    p = ctx.Process(target=_child, args=(qq, store, str(path), shape, regime, probe_ids, probe_keys, top_k, n))
    p.start()
    p.join()
    return qq.get() if not qq.empty() else (float("nan"), float("nan"), float("nan"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=5_000_000)
    ap.add_argument("--probes", type=int, default=50)
    ap.add_argument("--top-k", type=int, default=50)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    if not (_have("polars") and _have("pyarrow") and _have("numpy")):
        print("Need polars + pyarrow + numpy", file=sys.stderr)
        return 2
    import numpy as np

    stores = ["memory", "parquet"] + (["lance"] if _have("lance") else [])
    if "lance" not in stores:
        print("NOTE: pylance not installed -> memory+parquet only\n")

    tmp = Path(tempfile.mkdtemp(prefix="gm_match1_"))
    print(f"rows={args.rows:,} probes={args.probes} top_k={args.top_k} stores={stores}\n  writing base ...")
    df = generate_skewed(args.rows)
    n = df.height
    # Block-size skew report + probe keys drawn proportional to block mass.
    vc = df.group_by("block_key").len().sort("len", descending=True)
    sizes = vc["len"].to_list()
    print(f"  block-size skew: p50={sizes[len(sizes)//2]} p99={sizes[max(0,len(sizes)//100)]} max={sizes[0]} n_blocks={len(sizes):,}")
    rng = np.random.default_rng(99)
    probe_keys = list(rng.choice(vc["block_key"].to_list(), size=args.probes))
    probe_ids = list(range(args.probes))
    paths = write_stores(df, tmp)
    paths["memory"] = paths["parquet"]  # memory store loads from the parquet file
    del df

    print("\n" + "=" * 84)
    print(f"  {'shape/regime':<20} " + " | ".join(f"{s:^18}" for s in stores))
    print("  " + "-" * 82)

    def run(shape, regime):
        cells = []
        for s in stores:
            med, total, rss = measure(s, paths[s], shape, regime, probe_ids, probe_keys, args.top_k, n)
            cells.append(f"{med*1000:6.1f}ms/p {rss:6.0f}MB")
        print(f"  {shape+' '+regime:<20} " + " | ".join(cells))

    run("ann", "stream")
    run("block", "stream")
    run("ann", "microbatch")
    print("=" * 84)
    print("cells: per-probe median latency  peak-RSS(VmHWM).  memory store holds the full base;")
    print("parquet/lance serve from disk. Win = lance latency near memory with RSS near zero.")

    if not args.keep:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
