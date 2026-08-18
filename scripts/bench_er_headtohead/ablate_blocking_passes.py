"""Which blocking passes earn their comparisons?

Auto-config's probabilistic plan for person@100K is eight passes costing
121,391,850 comparisons, and the cost is not spread evenly. Scored against the
budget that already exists (`_blocking_pairs_per_row_budget`, default 50,
documented as "the scale-invariance knob ... keeps the total pair count
linear"):

    pass                              max block  pairs/row      comparisons
    [city, first_name]                        6          2           20,186
    [city, first_name] +substring5            8          3           22,740
    [first_name] soundex                  2,170      1,084       31,321,187
    [surname] soundex                     1,282        640       12,286,109
    [surname] substring5                  1,142        570        6,138,978
    [dob]                                    16          7          218,234
    [dob] substring:0:4  (birth YEAR)     1,549        774       71,337,394
    [postcode]                                9          4           47,022
                                                          TOTAL 121,391,850

Four of eight passes run 11-22x over the budget and carry 99.7% of the work.
That budget is applied when selecting a primary KEY and never to multi_pass
passes, and `_diversify_probabilistic_blocking` gates on block SIZE (a 7,071
row cap) where the cost is pair COUNT -- birth-year clears the size gate at
1,549 rows while contributing 59% of every comparison in the run.

The tempting move is to enforce the budget on passes. That is not safe to
deduce: dropping the over-budget passes removes the phonetic keys, and a plan
without them is what `rule_low_reduction_ratio` already produced by accident on
this shape -- pairwise recall 0.4684. A budget tuned to pick ONE bounding key
is not obviously the right bound for a UNION of complementary passes.

So this measures it instead. For each pass, drop that pass alone and report
what the run loses and what it saves. A pass that costs 71M comparisons and
buys no recall should go; one that costs 31M and holds recall up is doing its
job.

## Protocol

* One `auto_configure_probabilistic_df` config, built once, then varied by
  removing passes -- so every arm differs ONLY in the blocking plan.
* `comparisons` is `measure_blocking_profile(...).total_comparisons`: the true
  candidate count on the full frame, NOT `DedupeResult.scored_pairs`, which is
  the RETAINED post-cut set (~850x smaller here) and is the number that made
  this look like a measurement bug in the first place.
* Accuracy is pairwise P/R/F1 from `evaluate_clusters` against the fixture's
  committed ground truth.
* Run with the native kernel available. Pure Python scores 121M comparisons in
  well over 500 s; CI does the same work in ~27 s.

## Measured

Two scales so far, both pure-Python (native unavailable locally), deltas vs the
full 8-pass plan. Negative dF1 means the pass was earning its keep.

    4,001 rows / 973 true pairs           cmp saved       dF1        dR
    [dob] substring:0:4                     114,831   +0.0000   +0.0000
    [first_name] soundex                     69,273   +0.0011   +0.0000
    [surname] soundex                        20,147   +0.0000   +0.0000
    [surname] substring5                     10,653   +0.0000   +0.0000
    [postcode]                                  953   -0.0157   -0.0328

    10,001 rows / 2,428 true pairs         cmp saved       dF1        dR
    [dob] substring:0:4                     716,613   +0.0000   +0.0000
    [first_name] soundex                    388,266   +0.0000   +0.0000
    [surname] soundex                       118,088   -0.0004   +0.0021
    [surname] substring5                     59,694   +0.0000   +0.0000
    [postcode]                                2,520   -0.0142   -0.0247
    in-budget passes only                 1,282,661   -0.0025   -0.0020

The over-budget passes are ~free to remove at both scales, and the single pass
carrying recall is `postcode` -- IN budget, 2,520 comparisons. The bulk arm
drops 99.3% of all comparisons for dF1 -0.0025.

**Do not generalize this to 100K/1M yet.** Small frames are exactly where
phonetic keys have least to do: names collide as N grows, so `[first_name]
soundex` and `[surname] soundex` may start paying for themselves at a scale
these runs cannot see. Birth-year is the one result that looks scale-stable so
far (53% of comparisons at 4K, 55% at 10K, 59% at 100K, zero F1 either way at
the two scales measured). The 100K arm decides.

Usage:
    python scripts/bench_er_headtohead/ablate_blocking_passes.py \
        --input fixtures/bench_100000.parquet \\
        --ground-truth fixtures/bench_100000.truth.parquet \\
        --out ablation.json
"""
from __future__ import annotations

import argparse
import json
import time
from itertools import combinations
from pathlib import Path


def _sig(p) -> str:
    return f"{'+'.join(p.fields)} [{','.join(p.transforms or [])}]"


def _ground_truth_pairs(path: Path) -> set[tuple]:
    """{record_id, cluster_id} parquet -> the set of true within-cluster pairs."""
    import pyarrow.parquet as pq

    tbl = pq.read_table(path)
    cols = {c.lower(): c for c in tbl.column_names}
    rid = cols.get("record_id") or tbl.column_names[0]
    cid = cols.get("cluster_id") or tbl.column_names[1]
    by_cluster: dict = {}
    for r, c in zip(tbl.column(rid).to_pylist(), tbl.column(cid).to_pylist()):
        by_cluster.setdefault(c, []).append(r)
    out: set[tuple] = set()
    for members in by_cluster.values():
        if len(members) < 2:
            continue
        for a, b in combinations(sorted(members), 2):
            out.add((a, b))
    return out


def _run_arm(df, cfg, truth: set[tuple], label: str, dropped: str | None) -> dict:
    import goldenmatch
    from goldenmatch.core.blocker import measure_blocking_profile
    from goldenmatch.core.evaluate import evaluate_clusters

    prof = measure_blocking_profile(df, cfg)
    comparisons = int(prof.total_comparisons) if prof is not None else -1
    n_blocks = int(prof.n_blocks) if prof is not None else -1

    t0 = time.perf_counter()
    res = goldenmatch.dedupe_df(df, config=cfg)
    wall = time.perf_counter() - t0

    ev = evaluate_clusters(res.clusters, truth).summary()
    row = {
        "arm": label,
        "dropped_pass": dropped,
        "n_passes": len(list(cfg.blocking.passes or [])),
        "comparisons": comparisons,
        "n_blocks": n_blocks,
        "dedupe_wall_seconds": round(wall, 2),
        "precision": round(float(ev["precision"]), 4),
        "recall": round(float(ev["recall"]), 4),
        "f1": round(float(ev["f1"]), 4),
        "n_clusters": len(res.clusters),
    }
    print(
        f"[ablate] {label:<44s} passes={row['n_passes']} "
        f"cmp={comparisons:>13,} wall={row['dedupe_wall_seconds']:>7.2f}s "
        f"P={row['precision']:.4f} R={row['recall']:.4f} F1={row['f1']:.4f}",
        flush=True,
    )
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--ground-truth", required=True)
    ap.add_argument("--out", default="ablation.json")
    ap.add_argument(
        "--budget", type=int, default=None,
        help="pairs/row budget for labelling (defaults to the engine's own)",
    )
    args = ap.parse_args()

    import pyarrow.parquet as pq
    from goldenmatch.core.autoconfig import (
        _blocking_pairs_per_row_budget,
        _project_pairs_per_row,
        auto_configure_probabilistic_df,
    )

    df = pq.read_table(args.input)
    truth = _ground_truth_pairs(Path(args.ground_truth))
    print(f"[ablate] {df.num_rows:,} rows, {len(truth):,} true pairs", flush=True)

    cfg = auto_configure_probabilistic_df(df)
    passes = list(cfg.blocking.passes or [])
    budget = args.budget if args.budget is not None else _blocking_pairs_per_row_budget()
    print(f"[ablate] {len(passes)} passes, pairs/row budget = {budget}", flush=True)

    rows = [_run_arm(df, cfg, truth, "full (all passes)", None)]

    # Drop each pass ALONE. One-at-a-time so a pass that only matters in
    # combination is not written off by a bulk removal.
    for i, p in enumerate(passes):
        variant = cfg.model_copy(update={
            "blocking": cfg.blocking.model_copy(update={
                "passes": [q for j, q in enumerate(passes) if j != i],
            }),
        })
        rows.append(_run_arm(df, variant, truth, f"minus {_sig(p)}", _sig(p)))

    # And the bulk arm the budget would actually produce, so the "just enforce
    # it" option is measured rather than argued about.
    from goldenmatch.core.blocker import _build_static_blocks, _fast_static_block_sizes
    from goldenmatch.core.frame import to_frame

    frame = to_frame(df)
    keep = []
    for p in passes:
        sub = cfg.blocking.model_copy(update={
            "strategy": "static", "keys": [p], "passes": None, "auto_select": False,
        })
        fast = _fast_static_block_sizes(frame, sub)
        if fast is not None:
            sizes = list(fast[0])
        else:
            sizes = []
            for b in _build_static_blocks(frame, sub):
                try:
                    sizes.append(b.n_rows())
                except Exception:
                    sizes.append(0)
        if _project_pairs_per_row(max(sizes) if sizes else 0) <= budget:
            keep.append(p)
    if keep and len(keep) != len(passes):
        variant = cfg.model_copy(update={
            "blocking": cfg.blocking.model_copy(update={"passes": keep}),
        })
        rows.append(_run_arm(df, variant, truth, "in-budget passes only", "all over-budget"))

    Path(args.out).write_text(json.dumps({"rows": rows}, indent=2), encoding="utf-8")

    base = rows[0]
    print("\n[ablate] deltas vs full plan (negative F1 = the pass was earning its keep)")
    print(f"{'arm':<44s}{'cmp saved':>15s}{'dF1':>9s}{'dR':>9s}{'dP':>9s}")
    for r in rows[1:]:
        print(
            f"{r['arm']:<44s}{base['comparisons'] - r['comparisons']:>15,}"
            f"{r['f1'] - base['f1']:>+9.4f}{r['recall'] - base['recall']:>+9.4f}"
            f"{r['precision'] - base['precision']:>+9.4f}"
        )
    print(f"\n[ablate] wrote {args.out}")


if __name__ == "__main__":
    main()
