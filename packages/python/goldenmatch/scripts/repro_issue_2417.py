#!/usr/bin/env python3
"""#2417 micro-repro: peak RSS of scoring ONE oversized FS block, whole vs tiled.

The 14M-row identity-resolve OOM bisected to a single *hub* block (~6,300 rows
sharing one blocking-key value, ~19.8M intra-block candidate pairs) that
``_split_oversized`` cannot split, so ``score_buckets`` scored it WHOLE in one
call. This script isolates that one call — the whole point is that the block,
not the 14M-row run, is the thing worth measuring, so it fits on a laptop
instead of a 32GB box.

It drives the SHIPPED ``_score_block_bounded`` with the same per-lane scorer the
bucket lanes use, so ``--budget 0`` is literally the pre-#2417 whole-block call
and ``--budget N`` is literally the tiled one.

Usage:
    python scripts/repro_issue_2417.py --rows 6300 --lane native --budget 0
    python scripts/repro_issue_2417.py --rows 6300 --lane native --budget 4000000

Measured (2026-08-05, 16-core Windows box, in-tree release native build):

    lane        rows   candidates  surviving   budget    wall    peak delta
    vectorized  3,500   6,123,250     11,918   whole    3.03s       866 MB
    vectorized  3,500   6,123,250     11,918     4M     3.81s       562 MB
    vectorized  3,500   6,123,250     11,918     1M     6.14s       144 MB
    native      6,300  19,841,850     53,104   whole    0.39s        16 MB
    native      6,300  19,841,850     53,104     4M     1.35s        15 MB
    native      6,300  19,841,850  2,419,280   whole    8.69s       654 MB
    native      6,300  19,841,850  2,419,280     4M    19.24s       478 MB

Reading of that, which is NOT what the issue assumed:

* On the VECTORIZED (numpy) lane the peak is the dense ``n x n`` matrices —
  exactly ``O(n^2)`` in the block's row count (866/434 == (3500/2500)^2), and
  independent of how many pairs survive. Tiling bounds it to ~``16 x budget``
  bytes per field. This is the big win, and it also makes blocks that
  ``_fs_vec_guard`` currently REFUSES (>7,071 rows) scoreable at all.
* On the NATIVE lane the kernel threshold-filters per pair, so the ~19.8M
  candidates are never materialized: the peak tracks SURVIVING pairs, not
  candidates (16 MB at 53K survivors; 654 MB at 2.4M). Tiling only removes the
  whole-block double-buffering (Rust Vec -> pyo3 list -> rounded list), worth
  ~27%; the accumulated survivor list is the floor and tiling cannot move it.
  Bounding THAT needs pair streaming / arrow emission, not chunking.

Tiling costs ~2.2x the pair comparisons on the tiled block (cross tiles
recompute the intra-group pairs they discard) — paid only above the budget.
"""
from __future__ import annotations

import argparse
import os
import random
import threading
import time

FIRST = [
    "John", "Jon", "Jonathan", "Jane", "Janet", "Robert", "Bob", "Bobby",
    "Michael", "Mike", "William", "Bill", "Elizabeth", "Liz", "Beth",
    "Katherine", "Kate", "Kathy", "Christopher", "Chris", "Daniel", "Dan",
    "Matthew", "Matt", "Andrew", "Drew", "Joseph", "Joe", "Thomas", "Tom",
]
LAST = [
    "Smith", "Smyth", "Johnson", "Jonson", "Williams", "Brown", "Jones",
    "Miller", "Davis", "Garcia", "Rodriguez", "Wilson", "Martinez", "Anderson",
    "Taylor", "Thomas", "Moore", "Jackson", "Martin", "Lee",
]


def _rss_mb() -> float:
    import psutil

    return psutil.Process().memory_info().rss / 1e6


class PeakSampler:
    """Sample RSS on a background thread (5ms) around a call."""

    def __init__(self, interval: float = 0.005) -> None:
        self.interval = interval
        self.peak = 0.0
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    def __enter__(self) -> PeakSampler:
        self.baseline = _rss_mb()
        self.peak = self.baseline

        def _loop() -> None:
            while not self._stop.is_set():
                cur = _rss_mb()
                if cur > self.peak:
                    self.peak = cur
                self._stop.wait(self.interval)

        self._t = threading.Thread(target=_loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._t is not None:
            self._t.join(timeout=2)

    @property
    def delta_mb(self) -> float:
        return self.peak - self.baseline


def build_hub_block(n: int, dup_rate: float, seed: int = 17):
    """One block: every row shares ``__block_key__``; ``dup_rate`` of the rows
    are near-duplicates of an earlier row (name typo) so a realistic fraction of
    the C(n,2) candidates clears the link threshold."""
    import pyarrow as pa

    rng = random.Random(seed)
    first: list[str] = []
    last: list[str] = []
    zips: list[str] = []
    for i in range(n):
        if i and rng.random() < dup_rate:
            j = rng.randrange(i)
            f, la = first[j], last[j]
            if rng.random() < 0.5 and len(f) > 3:
                f = f[:-1]  # typo
            first.append(f)
            last.append(la)
            zips.append(zips[j])
        else:
            first.append(rng.choice(FIRST))
            last.append(rng.choice(LAST))
            zips.append(f"{rng.randrange(10000, 99999)}")
    return pa.table(
        {
            "__row_id__": pa.array(list(range(1, n + 1)), type=pa.int64()),
            "__block_key__": pa.array(["HUB"] * n, type=pa.large_string()),
            "first_name": pa.array(first, type=pa.large_string()),
            "last_name": pa.array(last, type=pa.large_string()),
            "zip": pa.array(zips, type=pa.large_string()),
        }
    )


def make_mk_and_em(table):
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
    from goldenmatch.core.probabilistic import train_em

    mk = MatchkeyConfig(
        name="fs",
        type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3,
                          partial_threshold=0.85),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=3,
                          partial_threshold=0.85),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
    )
    import polars as pl

    train_df = pl.from_arrow(table)
    em = train_em(train_df, mk)
    return mk, em


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=6300)
    ap.add_argument("--dup-rate", type=float, default=0.3)
    ap.add_argument("--lane", choices=["native", "vectorized"], default="native")
    ap.add_argument(
        "--budget", type=int, default=0,
        help="GOLDENMATCH_FS_OVERSIZED_CHUNK_PAIRS; 0 = score the block whole "
             "(the pre-#2417 behaviour)",
    )
    args = ap.parse_args()

    if args.lane == "vectorized":
        os.environ["GOLDENMATCH_FS_NATIVE"] = "0"
    os.environ["GOLDENMATCH_FS_OVERSIZED_CHUNK_PAIRS"] = str(args.budget)

    # Measure the SHIPPED basis: the same _score_block_bounded the bucket lanes
    # call for an un-splittable oversized block, wrapping the same per-lane
    # scorer they use.
    from goldenmatch.backends.score_buckets import _score_block_bounded
    from goldenmatch.core.probabilistic import (
        _fs_native_enabled,
        probabilistic_block_scorer,
        score_probabilistic_bucket_native,
    )

    table = build_hub_block(args.rows, args.dup_rate)
    mk, em = make_mk_and_em(table)
    n = table.num_rows
    candidates = n * (n - 1) // 2

    if args.lane == "native":
        def score_one(f):
            return score_probabilistic_bucket_native(f, [len(f)], mk, em, None)
    else:
        import polars as pl

        _scorer = probabilistic_block_scorer(mk, em)

        def score_one(f):
            return _scorer(f if not hasattr(f, "num_rows") else pl.from_arrow(f), None)

    print(f"lane={args.lane} native_enabled={_fs_native_enabled()} "
          f"budget={args.budget or 'whole-block'}")
    print(f"rows={n:,}  candidate pairs=C(n,2)={candidates:,}")

    import gc

    gc.collect()
    with PeakSampler() as ps:
        t0 = time.perf_counter()
        pairs = _score_block_bounded(table, n, score_one, tile_above_rows=0)
        wall = time.perf_counter() - t0
    print(
        f"surviving pairs={len(pairs):,} "
        f"({100.0 * len(pairs) / max(candidates, 1):.2f}% of candidates)"
    )
    print(f"wall={wall:.2f}s  baseline RSS={ps.baseline:.0f}MB  "
          f"peak RSS={ps.peak:.0f}MB  delta={ps.delta_mb:.0f}MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
