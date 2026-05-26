"""Micro-benchmark for the goldenmatch._native block-scoring kernel.

Answers one question the Arrow-ABI decision hinges on: of the wall spent on the
native block-scoring path, how much is *marshalling* (building Python lists via
`.to_list()` + PyO3 cloning them into `Vec<Vec<Option<String>>>`) versus
*irreducible compute* (rapidfuzz scoring)? An Arrow-native kernel ABI would
remove the marshalling (zero-copy buffer access) but not the compute.

Decomposition (per scale N, median of REPEATS runs):
  T_tolist  : Polars Series -> Python list materialization (row_ids + fields)
  T_ingest  : native call with ALL-SINGLETON blocks -> PyO3 arg ingest, ~0 compute
  T_full    : native call with real block sizes -> ingest + compute
  T_compute : T_full - T_ingest (the rapidfuzz work, irreducible)
  T_pyloop  : the pure-Python per-pair loop (score_buckets fallback branch)

Arrow upside ceiling  = T_tolist + T_ingest   (what zero-copy could remove)
Irreducible            = T_compute
Native speedup vs py   = T_pyloop / T_full

Run:  python scripts/bench_native_kernels.py [N ...]
"""
from __future__ import annotations

import random
import statistics
import sys
import time

import polars as pl

import goldenmatch._native as native

# jaro_winkler=0, token_sort=2 (see backends/score_buckets._NATIVE_SCORER_IDS)
SCORER_IDS = [0, 2]
WEIGHTS = [1.0, 1.0]
TOTAL_WEIGHT = 2.0
THRESHOLD = 0.6
AVG_BLOCK = 3
REPEATS = 5

_FIRST = ["John", "Jon", "Jane", "Jayne", "Robert", "Bob", "Mary", "Maria",
          "William", "Will", "Elizabeth", "Liz", "Michael", "Mike", "Sarah"]
_LAST = ["Smith", "Smyth", "Doe", "Roe", "Johnson", "Jonson", "Williams",
         "Brown", "Browne", "Taylor", "Tailor", "Anderson", "Andersen"]
_CITY = ["London", "Londng", "Leeds", "York", "Bristol", "Bristl", "Bath",
         "Oxford", "Cambridge", "Cambrdge", "Manchester", "Leicester"]


def _make_workload(n: int, seed: int = 7) -> tuple[pl.DataFrame, list[str], list[str]]:
    rng = random.Random(seed)
    n_blocks = max(1, n // AVG_BLOCK)
    block_keys = [rng.randint(0, n_blocks - 1) for _ in range(n)]
    names = [f"{rng.choice(_FIRST)} {rng.choice(_LAST)}" for _ in range(n)]
    cities = [rng.choice(_CITY) for _ in range(n)]
    df = pl.DataFrame({
        "__row_id__": list(range(n)),
        "__block_key__": block_keys,
        "name": names,
        "city": cities,
    }).sort("__block_key__")
    return df, names, cities


def _block_sizes(df: pl.DataFrame) -> list[int]:
    return (
        df.lazy().group_by("__block_key__", maintain_order=True)
        .agg(pl.len().alias("s")).collect()["s"].to_list()
    )


def _py_loop(row_ids, sizes, fields, weights, total_weight, threshold):
    from rapidfuzz.distance import JaroWinkler
    from rapidfuzz.fuzz import token_sort_ratio
    fns = [JaroWinkler.similarity, lambda a, b: token_sort_ratio(a, b) / 100.0]
    out = []
    offset = 0
    for size in sizes:
        if size >= 2:
            end = offset + size
            for i in range(offset, end - 1):
                ri = row_ids[i]
                for j in range(i + 1, end):
                    rj = row_ids[j]
                    pk = (ri, rj) if ri < rj else (rj, ri)
                    ss = ws = 0.0
                    for f in range(len(fns)):
                        va, vb = fields[f][i], fields[f][j]
                        if va is None or vb is None:
                            continue
                        ss += fns[f](va, vb) * weights[f]
                        ws += weights[f]
                    if ws > 0:
                        c = ss / total_weight
                        if c >= threshold:
                            out.append((pk[0], pk[1], c))
        offset += size
    return out


def _median(fn, repeats=REPEATS):
    samples = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t0)
    return statistics.median(samples)


def bench(n: int) -> None:
    df, _names, _cities = _make_workload(n)
    sizes = _block_sizes(df)
    singletons = [1] * n
    n_pairs = sum(s * (s - 1) // 2 for s in sizes)

    def build_lists():
        rid = df["__row_id__"].to_list()
        fa = [df["name"].to_list(), df["city"].to_list()]
        return rid, fa

    t_tolist = _median(build_lists)
    row_ids, field_arrays = build_lists()

    def call_ingest():
        native.score_block_pairs(row_ids, singletons, field_arrays, SCORER_IDS,
                                 WEIGHTS, TOTAL_WEIGHT, THRESHOLD, [])

    def call_full():
        return native.score_block_pairs(row_ids, sizes, field_arrays, SCORER_IDS,
                                        WEIGHTS, TOTAL_WEIGHT, THRESHOLD, [])

    t_ingest = _median(call_ingest)
    t_full = _median(call_full)
    t_compute = max(0.0, t_full - t_ingest)
    t_pyloop = _median(
        lambda: _py_loop(row_ids, sizes, field_arrays, WEIGHTS, TOTAL_WEIGHT, THRESHOLD),
        repeats=3,
    )

    # Arrow-native path: hand Polars' Arrow buffers to the kernel zero-copy.
    def build_arrow():
        rid = df["__row_id__"].to_arrow()
        fa = [df["name"].to_arrow(), df["city"].to_arrow()]
        return rid, fa

    t_arrow_build = _median(build_arrow)
    arrow_row, arrow_fields = build_arrow()

    def call_arrow():
        return native.score_block_pairs_arrow(arrow_row, arrow_fields, sizes, SCORER_IDS,
                                              WEIGHTS, TOTAL_WEIGHT, THRESHOLD, [])

    t_arrow_call = _median(call_arrow)

    native_call = t_tolist + t_full          # what score_buckets pays today (Vec kernel)
    arrow_ceiling = t_tolist + t_ingest      # theoretical max removable by zero-copy ABI
    arrow_path = t_arrow_build + t_arrow_call  # measured Arrow-native path
    py_total = t_tolist + t_pyloop

    print(f"\n=== N={n:,} rows, {len(sizes):,} blocks, {n_pairs:,} candidate pairs ===")
    print(f"  T_tolist   (.to_list materialization) : {t_tolist*1e3:8.1f} ms")
    print(f"  T_ingest   (PyO3 arg clone, ~0 compute): {t_ingest*1e3:8.1f} ms")
    print(f"  T_full     (ingest + rapidfuzz compute): {t_full*1e3:8.1f} ms")
    print(f"  T_compute  (= full - ingest)           : {t_compute*1e3:8.1f} ms")
    print(f"  T_pyloop   (pure-Python per-pair loop) : {t_pyloop*1e3:8.1f} ms")
    print(f"  T_arrowbuild (.to_arrow zero-copy)     : {t_arrow_build*1e3:8.1f} ms")
    print(f"  T_arrowcall  (arrow kernel: ingest+comp): {t_arrow_call*1e3:8.1f} ms")
    print(f"  --")
    print(f"  Vec path total   (tolist+full)         : {native_call*1e3:8.1f} ms")
    print(f"  Arrow path total (arrowbuild+arrowcall): {arrow_path*1e3:8.1f} ms")
    print(f"  ARROW SPEEDUP vs Vec kernel            : {native_call/arrow_path:8.2f}x"
          f"  ({100*(1-arrow_path/native_call):4.1f}% wall cut)")
    print(f"  Arrow upside ceiling (tolist+ingest)   : {arrow_ceiling*1e3:8.1f} ms"
          f"  ({100*arrow_ceiling/native_call:4.1f}% of Vec path)")
    print(f"  irreducible compute fraction           : {100*t_compute/native_call:4.1f}%")
    print(f"  native(Vec) speedup vs python          : {t_pyloop/t_full:8.2f}x")


if __name__ == "__main__":
    scales = [int(x) for x in sys.argv[1:]] or [100_000, 1_000_000]
    print("goldenmatch._native block-scoring kernel micro-benchmark")
    print(f"scorers={SCORER_IDS} avg_block={AVG_BLOCK} threshold={THRESHOLD} repeats={REPEATS}")
    for n in scales:
        bench(n)
