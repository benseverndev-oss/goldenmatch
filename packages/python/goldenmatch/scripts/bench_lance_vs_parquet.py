#!/usr/bin/env python3
"""Lance vs Parquet bake-off for GoldenMatch's candidate-retrieval patterns.

Standalone (does NOT import ``goldenmatch``) so it runs anywhere the bench env
has polars + pyarrow (+ optionally lance) installed. If ``lance`` is missing the
script runs Parquet-only and prints an install hint rather than failing.

Motivation: see docs/superpowers/specs/2026-06-13-lance-vs-parquet-candidate-
retrieval-design.md. We measure three on-disk read shapes that mirror how the
pipeline reads its working set back:

  * full_scan    -- read the scoring columns end-to-end (baseline).
  * block_filter -- predicate-retrieve one block (blocker.py:283 group-by, on
                    disk a ``WHERE __block_key__ = X``).
  * scatter_take -- gather K non-contiguous row indices (the ANN sub-blocking
                    path, blocker.py:486 ``_ann_``). This is the pattern Lance's
                    random-access ``take`` should win; Parquet must scan the
                    column(s) and gather.

Per the performance-audit lesson we report **5-run median wall on real shapes**,
and isolate peak RSS per measurement by running each in a forked child process.

Usage:
    python scripts/bench_lance_vs_parquet.py --rows 10_000_000 \
        --candidates-frac 0.001 --runs 5
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

# Columns the scorer actually touches -- name/address text + a couple numerics.
SCORE_COLS = ["id", "block_key", "name", "address", "age", "score_hint"]


def _have(mod: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(mod) is not None


def generate(rows: int, seed: int = 7):
    """Synthetic records: bounded-cardinality block key + text/numeric fields."""
    import numpy as np
    import polars as pl

    rng = np.random.default_rng(seed)
    # zip-like bounded cardinality (~40K real US zips) -> blocks grow with N.
    n_zips = min(40_000, max(1, rows // 25))
    block_key = rng.integers(0, n_zips, size=rows)
    first = rng.integers(0, 5000, size=rows)
    street = rng.integers(0, 9999, size=rows)
    df = pl.DataFrame(
        {
            "id": np.arange(rows, dtype=np.int64),
            "block_key": [f"{z:05d}" for z in block_key],
            "name": [f"person_{a}_{b}" for a, b in zip(first, rng.integers(0, 5000, size=rows))],
            "address": [f"{s} main st apt {a}" for s, a in zip(street, rng.integers(0, 999, size=rows))],
            "age": rng.integers(18, 95, size=rows).astype("int32"),
            "score_hint": rng.random(size=rows).astype("float32"),
        }
    )
    # Sort on block_key: the FAIR Parquet layout (row-group stats localize blocks).
    return df.sort("block_key")


def write_formats(df, root: Path) -> dict[str, Path]:
    import polars as pl  # noqa: F401

    paths: dict[str, Path] = {}
    pq = root / "dataset.parquet"
    t0 = time.perf_counter()
    df.write_parquet(pq, row_group_size=128 * 1024, statistics=True)
    print(f"  parquet write: {time.perf_counter() - t0:6.2f}s  size={pq.stat().st_size / 1e6:8.1f} MB")
    paths["parquet"] = pq

    if _have("lance"):
        import lance
        import pyarrow as pa

        lpath = root / "dataset.lance"
        # Polars exports Utf8 as Arrow `large_string`, which Lance's BTREE scalar
        # index rejects. Cast block_key (the indexed column) to plain `string`.
        tbl = df.to_arrow()
        if pa.types.is_large_string(tbl.schema.field("block_key").type):
            ci = tbl.schema.get_field_index("block_key")
            tbl = tbl.set_column(ci, "block_key", tbl.column("block_key").cast(pa.string()))
        t0 = time.perf_counter()
        lance.write_dataset(tbl, str(lpath))
        size = sum(f.stat().st_size for f in lpath.rglob("*") if f.is_file())
        print(f"  lance   write: {time.perf_counter() - t0:6.2f}s  size={size / 1e6:8.1f} MB")
        paths["lance"] = lpath
    return paths


# ---- read workloads (one per engine x pattern) --------------------------------

def _parquet_full(path: Path):
    import polars as pl

    return pl.read_parquet(path, columns=SCORE_COLS).height


def _parquet_block(path: Path, key: str):
    import polars as pl

    return (
        pl.scan_parquet(path)
        .filter(pl.col("block_key") == key)
        .select(SCORE_COLS)
        .collect()
        .height
    )


def _parquet_take(path: Path, idx):
    import polars as pl

    # Status quo: Parquet has no random row access -> read column(s), then gather.
    df = pl.read_parquet(path, columns=SCORE_COLS)
    return df[idx].height


def _lance_full(path: Path):
    import lance

    return lance.dataset(str(path)).to_table(columns=SCORE_COLS).num_rows


def _lance_block(path: Path, key: str):
    import lance

    ds = lance.dataset(str(path))
    return ds.scanner(columns=SCORE_COLS, filter=f"block_key = '{key}'").to_table().num_rows


def _lance_take(path: Path, idx):
    import lance

    ds = lance.dataset(str(path))
    return ds.take(list(idx), columns=SCORE_COLS).num_rows


# Same scan as _lance_block; the scanner transparently uses a BTREE scalar
# index on block_key if one was built (the indexed variant).
_lance_block_idx = _lance_block


def build_lance_scalar_index(path: Path, column: str = "block_key") -> float:
    """Build a BTREE scalar index on `column`; return wall seconds."""
    import lance

    ds = lance.dataset(str(path))
    t0 = time.perf_counter()
    ds.create_scalar_index(column, "BTREE")
    return time.perf_counter() - t0


WORKLOADS = {
    ("parquet", "full_scan"): _parquet_full,
    ("parquet", "block_filter"): _parquet_block,
    ("parquet", "scatter_take"): _parquet_take,
    ("lance", "full_scan"): _lance_full,
    ("lance", "block_filter"): _lance_block,
    ("lance", "block_filter_idx"): _lance_block_idx,
    ("lance", "scatter_take"): _lance_take,
}


def _peak_rss_mb() -> float:
    """Peak resident set of THIS process, in MB.

    Use /proc/self/status VmHWM on Linux: a spawned child's
    ``resource.ru_maxrss`` inherits the PARENT's high-water mark (verified —
    every child floored at the parent's peak), so it cannot isolate per-read
    footprint. VmHWM is per-process correct. Fall back to ru_maxrss elsewhere.
    """
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) / 1024.0  # kB -> MB
    except OSError:
        pass
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / 1024.0 / 1024.0 if sys.platform == "darwin" else raw / 1024.0


def _child(q, fn_key, path_str, arg, runs):
    """Run one workload `runs` times in an isolated process; report wall+RSS."""
    fn = WORKLOADS[fn_key]
    path = Path(path_str)
    walls = []
    for _ in range(runs):
        t0 = time.perf_counter()
        if arg is None:
            fn(path)
        else:
            fn(path, arg)
        walls.append(time.perf_counter() - t0)
    q.put((statistics.median(walls), min(walls), _peak_rss_mb()))


def measure(engine, pattern, path: Path, arg, runs: int):
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_child, args=(q, (engine, pattern), str(path), arg, runs))
    p.start()
    p.join()
    if not q.empty():
        return q.get()
    return (float("nan"), float("nan"), float("nan"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument(
        "--candidates-frac",
        type=float,
        nargs="+",
        default=[1e-4, 1e-3, 1e-2],
        help="scatter_take candidate fraction(s) of N (the ANN gather size)",
    )
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--keep", action="store_true", help="keep the generated datasets")
    args = ap.parse_args()

    if not _have("polars") or not _have("pyarrow") or not _have("numpy"):
        print("Need polars + pyarrow + numpy. Install: pip install polars pyarrow numpy", file=sys.stderr)
        return 2
    has_lance = _have("lance")
    engines = ["parquet"] + (["lance"] if has_lance else [])
    if not has_lance:
        print("NOTE: `lance` not installed -> Parquet-only run. Install: pip install pylance\n")

    import numpy as np

    tmp = Path(tempfile.mkdtemp(prefix="gm_lance_bench_"))
    print(f"rows={args.rows:,}  runs={args.runs}  engines={engines}  dir={tmp}")
    print("generating + writing ...")
    df = generate(args.rows)
    # Pick a real block key (most populous) and candidate index sets up front.
    top_key = (
        df.group_by("block_key").len().sort("len", descending=True).select("block_key").head(1).item()
    )
    paths = write_formats(df, tmp)
    n = df.height
    del df  # free before forking read children so RSS reflects the read only

    rng = np.random.default_rng(11)
    cand_sets = {frac: np.sort(rng.choice(n, size=max(1, int(n * frac)), replace=False)) for frac in args.candidates_frac}

    def row(label, results):
        base = results.get("parquet", (float("nan"),))[0]
        cells = []
        for eng in engines:
            med, mn, rss = results[eng]
            x = f"{base / med:5.1f}x" if (eng == "lance" and med and not _isnan(med)) else "  -  "
            cells.append(f"{med * 1000:9.1f}ms {x} {rss:7.0f}MB")
        print(f"  {label:<22} " + " | ".join(cells))

    print("\n" + "=" * 78)
    hdr = "  {:<22} ".format("pattern") + " | ".join(f"{e:^28}" for e in engines)
    print(hdr)
    print("  " + "-" * (len(hdr)))

    # full_scan
    res = {e: measure(e, "full_scan", paths[e], None, args.runs) for e in engines}
    row("full_scan", res)
    # block_filter — parquet (sorted) vs lance UN-indexed first (apples-to-apples
    # on raw scan), then lance WITH a BTREE scalar index built on block_key.
    res = {e: measure(e, "block_filter", paths[e], top_key, args.runs) for e in engines}
    row("block_filter", res)
    if has_lance:
        try:
            idx_build_s = build_lance_scalar_index(paths["lance"])
            print(f"  (built lance BTREE scalar index on block_key in {idx_build_s:.2f}s)")
            lance_idx = measure("lance", "block_filter_idx", paths["lance"], top_key, args.runs)
            row("block_filter (idx)", {"parquet": res["parquet"], "lance": lance_idx})
        except Exception as e:  # noqa: BLE001 - index API drift shouldn't abort the run
            print(f"  (lance scalar index step skipped: {type(e).__name__}: {e})")
    # scatter_take across fractions
    for frac, idx in cand_sets.items():
        res = {e: measure(e, "scatter_take", paths[e], idx, args.runs) for e in engines}
        row(f"scatter_take {frac:g} (K={len(idx)})", res)

    print("=" * 78)
    print("cells: median-wall  (lance x-factor vs parquet)  peak-RSS (VmHWM)")
    print("decision gate (see spec): adopt only if scatter_take >= 5x at frac<=1e-3 on >=10M rows,")
    print("and full_scan/block_filter within ~1.2x of parquet.")

    if not args.keep:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print(f"\nkept datasets in {tmp}")
    return 0


def _isnan(x) -> bool:
    return x != x


if __name__ == "__main__":
    raise SystemExit(main())
