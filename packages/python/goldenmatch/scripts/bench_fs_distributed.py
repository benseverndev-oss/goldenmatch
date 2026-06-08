#!/usr/bin/env python
"""Phase 3c bench: Fellegi-Sunter dedupe at scale on the bucket backend.

Validates that the Phase 3a FS-on-bucket path holds at 5M+ rows: measures wall
+ peak RSS, and F1 against KNOWN injected duplicate pairs (so accuracy is
checked, not just that it runs). The bucket backend is what carries the Ray /
DataFusion distribution wiring, so a healthy single-node bucket run is the
prerequisite for the distributed claim.

Synthetic shape: distributed surnames (no soundex collapse) + injected
near-duplicates (a typo'd name sharing the block key) whose (base, dup) row
indices ARE the ground truth — `dedupe_df` assigns `__row_id__` by input row
position, so the GT is exact in row-id space.

Usage:
    uv run python packages/python/goldenmatch/scripts/bench_fs_distributed.py \
        --rows 5000000 --dup-frac 0.2 --backend bucket

Emits machine-greppable `KEY=VALUE` lines (WALL_SECONDS, PEAK_RSS_GB, F1, ...)
plus a markdown block the workflow tees into the job summary.
"""
from __future__ import annotations

import argparse
import random
import resource
import sys
import time

import polars as pl

_SURN = [
    "smith", "jones", "taylor", "brown", "williams", "wilson", "johnson",
    "davies", "robinson", "wright", "thompson", "evans", "walker", "white",
    "roberts", "green", "hall", "wood", "harris", "martin", "jackson", "clarke",
    "clark", "turner", "hill", "scott", "cooper", "morris", "ward", "moore",
]
_FIRST = [
    "alexander", "benjamin", "charlotte", "daniel", "eleanor", "frederick",
    "grace", "harriet", "isabella", "jonathan", "katherine", "lawrence",
    "margaret", "nicholas", "olivia", "patricia", "quentin", "rebecca",
    "samuel", "theodore", "ursula", "victoria", "william", "zachary",
]


def _typo(s: str, rng: random.Random) -> str:
    if len(s) < 4:
        return s
    j = rng.randrange(len(s) - 1)
    return s[:j] + s[j + 1] + s[j] + s[j + 2:]  # adjacent transposition


def gen_with_gt(n: int, dup_frac: float, seed: int):
    """Return (df, ground_truth_pairs). __row_id__ == input row position, so
    the injected (base_idx, dup_idx) pairs are GT directly."""
    rng = random.Random(seed)
    n_zip = max(1, n // 40)
    rows: list[dict] = []
    for i in range(n):
        f, l = rng.choice(_FIRST), rng.choice(_SURN)
        rows.append({
            "first_name": f, "last_name": l,
            "email": f"{f}.{l}.{i}@example.com",
            "zip": f"{rng.randrange(n_zip):05d}",
        })
    gt: set[tuple[int, int]] = set()
    n_dups = int(n * dup_frac)
    for _ in range(n_dups):
        base_idx = rng.randrange(len(rows))
        src = rows[base_idx]
        dup_idx = len(rows)
        rows.append({
            "first_name": _typo(src["first_name"], rng),
            "last_name": src["last_name"],
            "email": src["email"],  # exact agree on email -> strong FS signal
            "zip": src["zip"],
        })
        gt.add((min(base_idx, dup_idx), max(base_idx, dup_idx)))
    # NOTE: order preserved (no shuffle) so row position == __row_id__ == GT idx.
    return pl.DataFrame(rows), gt


def _fs_cfg(backend: str):
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
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2,
                          partial_threshold=0.85),
            MatchkeyField(field="email", scorer="exact", levels=2),
        ])],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
        backend=backend,
    )


def _peak_rss_gb() -> float:
    # ru_maxrss is KiB on Linux.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024.0 * 1024.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=5_000_000)
    ap.add_argument("--dup-frac", type=float, default=0.2)
    ap.add_argument("--backend", default="bucket")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    print(f"[gen] {args.rows} base rows + {int(args.rows*args.dup_frac)} dups "
          f"(backend={args.backend})", flush=True)
    t_gen = time.perf_counter()
    df, gt = gen_with_gt(args.rows, args.dup_frac, args.seed)
    print(f"[gen] {df.height} total rows, {len(gt)} GT pairs in "
          f"{time.perf_counter()-t_gen:.1f}s", flush=True)

    from goldenmatch import dedupe_df
    from goldenmatch.core.evaluate import evaluate_clusters

    t0 = time.perf_counter()
    result = dedupe_df(df, config=_fs_cfg(args.backend))
    wall = time.perf_counter() - t0
    rss = _peak_rss_gb()

    clusters = result.clusters
    multi = sum(1 for c in clusters.values() if len(c.get("members", [])) > 1)
    ev = evaluate_clusters(clusters, gt)

    for k, v in [
        ("ROWS", df.height), ("BACKEND", args.backend),
        ("WALL_SECONDS", f"{wall:.3f}"), ("PEAK_RSS_GB", f"{rss:.2f}"),
        ("CLUSTERS_TOTAL", len(clusters)), ("CLUSTERS_MULTI", multi),
        ("GT_PAIRS", len(gt)),
        ("PRECISION", f"{ev.precision:.4f}"), ("RECALL", f"{ev.recall:.4f}"),
        ("F1", f"{ev.f1:.4f}"),
    ]:
        print(f"{k}={v}", flush=True)

    print("\n## bench-fs-distributed")
    print(f"- rows: **{df.height}**  backend: `{args.backend}`")
    print(f"- wall: **{wall:.1f}s**  peak RSS: **{rss:.2f} GB**")
    print(f"- multi-member clusters: {multi}  (GT pairs: {len(gt)})")
    print(f"- **P={ev.precision:.3f}  R={ev.recall:.3f}  F1={ev.f1:.3f}**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
