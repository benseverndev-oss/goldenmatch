"""CLEAR-KG Track B runner: generate a synthetic corpus, resolve mentions with
each engine, and report the clustering trio + the homograph split-rate gap.

    python benchmarks/clear-kg/run_track_b.py
    python benchmarks/clear-kg/run_track_b.py --n-entities 60 --homograph-pairs 15
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_HERE = Path(__file__).parent
_PKG_ROOT = _HERE.parent.parent
for p in (str(_HERE), str(_PKG_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("GOLDENMATCH_NATIVE", "0")
os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

from generate import generate_corpus  # noqa: E402
from score import score_track_b  # noqa: E402
from track_b import ENGINES  # noqa: E402


def run(corpus: dict, engines=("exact_surface", "goldenmatch"), threshold=None) -> dict:
    mentions = corpus["mentions"]
    gold = {m["mention_id"]: m["gold_entity_id"] for m in mentions}
    out = {}
    for eng in engines:
        fn = ENGINES[eng]
        pred = fn(mentions, threshold=threshold) if eng == "goldenmatch" else fn(mentions)
        out[eng] = score_track_b(pred, gold, mentions)
    return out


def main():
    ap = argparse.ArgumentParser(description="CLEAR-KG Track B (corpus-level ER)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--n-entities", type=int, default=20)
    ap.add_argument("--homograph-pairs", type=int, default=5)
    ap.add_argument("--docs-per-entity", type=int, default=3)
    ap.add_argument("--threshold", type=float, default=None,
                    help="goldenmatch ER accept threshold (default 0.5)")
    args = ap.parse_args()

    corpus = generate_corpus(
        seed=args.seed, n_entities=args.n_entities,
        n_homograph_pairs=args.homograph_pairs, docs_per_entity=args.docs_per_entity,
    )
    n_ent = len(corpus["entities"])
    n_men = len(corpus["mentions"])
    print(f"corpus: {n_ent} entities, {n_men} mentions, "
          f"{len(corpus['docs'])} docs, {len(corpus['homograph_surfaces'])} homograph surfaces")

    res = run(corpus, threshold=args.threshold)
    print(f"\n{'engine':16s} {'pair-F1':>8s} {'B3-F1':>7s} {'homograph split':>16s} "
          f"{'clusters':>9s}")
    for eng, s in res.items():
        print(f"{eng:16s} {s['pairwise_f1']:8.3f} {s['bcubed_f1']:7.3f} "
              f"{s['homograph_split_rate']:15.3f}  {s['n_pred_clusters']:8d}  "
              f"(gold {s['n_gold_entities']})")
    gm = res.get("goldenmatch", {})
    ex = res.get("exact_surface", {})
    if gm and ex:
        print(f"\nhomograph split-rate: goldenmatch {gm['homograph_split_rate']:.3f} "
              f"vs exact_surface {ex['homograph_split_rate']:.3f}  "
              f"(confusable pairs: {gm['homograph_confusable']})")


if __name__ == "__main__":
    main()
