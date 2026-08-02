#!/usr/bin/env python
"""IN-DISTRIBUTION sanity check for the local ER-matcher (1.5B) on PRODUCT data.

*** DO NOT read this as a SOTA comparison. ***

**CONTAMINATION WARNING:** ``amazon_google`` is one of the 1.5B's SIX TRAINING
sources (``scripts/er_matcher/sources.yaml`` -- bundle mechanism, not
``eval_only``, so ``build_corpus.py`` folds it into the training corpus). This
script therefore evaluates the model on (near-)TRAINING data -- it measures
memorization + in-distribution fit, NOT held-out generalization. The high number
it prints (this box, 2026-08-02, 250+250 hard set, seed 7: local 1.5B
P/R/F1 = 0.800/0.992/0.886 vs fuzzy 0.585/0.784/0.670) is an inflated
upper-bound and MUST NOT be compared to the published DeepMatcher (0.693) /
Ditto (0.756) figures, which are on a held-out canonical test split. (An earlier
version of this docstring wrongly claimed "above DeepMatcher/Ditto" -- retracted.)

The ONLY honest, held-out, comparable product number is **walmart_amazon
zero-shot F1 0.795** (walmart is NOT a training source; pinned in
``core/_llm_loader.py``): it BEATS DeepMatcher (0.669) and is BELOW Ditto
(0.868, which fine-tunes per-dataset). So the defensible claim is a competitive
local/private/zero-config scorer, not a SOTA-beating one.

To run a VALID product comparison, point this at a HELD-OUT dataset (e.g.
walmart_amazon, or abt_buy ONLY if abt_buy is removed from the training corpus)
and evaluate against the canonical DeepMatcher/Ditto test split -- not the
in-training amazon_google tables.

It measures the SCORER's discrimination on a BALANCED set of true matches + HARD
negatives (blocking-generated look-alikes) vs the fuzzy baseline on the SAME
pairs; not full end-to-end clustering. CPU-slow (~2.3s/pair); GPU or a smaller
model for larger runs.

Data: the Leipzig Amazon-Google CSVs under
``packages/python/goldenmatch/tests/benchmarks/datasets/Amazon-Google/``
(gitignored; the ``bench_er_headtohead`` datasets loader reads them). Model:
the pinned 1.5B GGUF, resolved by ``load_local_adapter``.

Usage:
    GOLDENMATCH_LOCAL_LLM=1 python scripts/er_matcher/bench_product_matching.py [--per-class 250]
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time


def _prf(pred: list[bool], y: list[int]) -> tuple[float, float, float]:
    tp = sum(1 for p, t in zip(pred, y) if p and t)
    fp = sum(1 for p, t in zip(pred, y) if p and not t)
    fn = sum(1 for p, t in zip(pred, y) if not p and t)
    P = tp / (tp + fp) if tp + fp else 0.0
    R = tp / (tp + fn) if tp + fn else 0.0
    return P, R, (2 * P * R / (P + R) if P + R else 0.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=250, help="matches + hard negatives each")
    ap.add_argument("--hard-neg-floor", type=float, default=0.80,
                    help="min fuzzy score for a non-match to count as a HARD negative")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    os.environ.setdefault("GOLDENMATCH_LOCAL_LLM", "1")

    _here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(_here, "..", "bench_er_headtohead"))
    import datasets as D  # type: ignore
    import polars as pl
    from goldenmatch import dedupe_df
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    from goldenmatch.core._llm_loader import load_local_adapter

    recs, truth = D.load_dataset("amazon_google")
    df = pl.from_arrow(recs)
    rid = df["record_id"].to_list()
    tmap = dict(zip(truth.column("record_id").to_pylist(),
                    truth.column("cluster_id").to_pylist()))
    gold = {i: tmap[r] for i, r in enumerate(rid)}
    df = df.drop("record_id")
    rows = {i: df.row(i, named=True) for i in range(len(rid))}
    cols = ["title", "manufacturer", "price"]

    def is_match(a: int, b: int) -> bool:
        return gold[a] == gold[b]

    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="m", type="weighted", threshold=0.5, fields=[
            MatchkeyField(field="title", scorer="jaro_winkler", weight=1.0, threshold=0.5)])],
        blocking=BlockingConfig(strategy="multi_pass", passes=[
            BlockingKeyConfig(fields=["manufacturer"], transforms=["lowercase", "strip"]),
            BlockingKeyConfig(fields=["title"], transforms=["lowercase", "substring:0:6"]),
        ]))
    r = dedupe_df(df, config=cfg, confidence_required=False, allow_red_config=True)
    sp = [(min(a, b), max(a, b), s) for a, b, s in r.scored_pairs]
    pos = [(a, b, s) for a, b, s in sp if is_match(a, b)]
    hard_neg = [(a, b, s) for a, b, s in sp if not is_match(a, b) and s >= args.hard_neg_floor]

    rng = random.Random(args.seed)
    K = args.per_class
    testset = ([(a, b, s, 1) for a, b, s in rng.sample(pos, min(K, len(pos)))]
               + [(a, b, s, 0) for a, b, s in rng.sample(hard_neg, min(K, len(hard_neg)))])
    y = [t for *_, t in testset]
    print(f"test set: {sum(y)} matches + {len(y) - sum(y)} hard negatives "
          f"(fuzzy>={args.hard_neg_floor}) = {len(testset)}", flush=True)

    # fuzzy baseline: best-threshold F1 over this set
    bestF = bestT = 0.0
    for ti in range(50, 99, 2):
        T = ti / 100
        f = _prf([s >= T for _a, _b, s, _t in testset], y)[2]
        if f > bestF:
            bestF, bestT = f, T
    bp = _prf([s >= bestT for _a, _b, s, _t in testset], y)
    print(f"FUZZY (best-T {bestT:.2f})  P/R/F1={bp[0]:.3f}/{bp[1]:.3f}/{bp[2]:.3f}", flush=True)

    adapter = load_local_adapter()
    if adapter is None:
        print("no local model available (set GOLDENMATCH_LOCAL_LLM=1 + install "
              "goldenmatch[local-llm], or GOLDENMATCH_LOCAL_LLM_PATH)")
        return
    t = time.perf_counter()
    llm_pred = []
    for i, (a, b, _s, _t) in enumerate(testset):
        is_m, _conf = adapter.score_pair(rows[a], rows[b], cols)
        llm_pred.append(is_m)
        if (i + 1) % 50 == 0:
            print(f"  scored {i + 1}/{len(testset)} ({time.perf_counter() - t:.0f}s)", flush=True)
    lp = _prf(llm_pred, y)
    print(f"\nLOCAL 1.5B  P/R/F1={lp[0]:.3f}/{lp[1]:.3f}/{lp[2]:.3f}  "
          f"({time.perf_counter() - t:.0f}s, {(time.perf_counter() - t) / len(testset):.1f}s/pair)")
    print(f"\n=> local 1.5B F1 {lp[2]:.3f} vs fuzzy {bp[2]:.3f}  "
          "[IN-DISTRIBUTION: amazon_google is a TRAINING source -- this is a "
          "memorization/sanity number, NOT comparable to held-out "
          "DeepMatcher/Ditto. Held-out product number: walmart_amazon 0.795.]")


if __name__ == "__main__":
    main()
