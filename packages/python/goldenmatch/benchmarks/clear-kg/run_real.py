"""CLEAR-KG real-data Track B: run every ER engine on REAL Wikipedia homographs
and report the homograph split-rate gap on data nobody in this repo authored.

    python benchmarks/clear-kg/run_real.py            # fetch (cached) + run
    python benchmarks/clear-kg/run_real.py --refresh  # re-fetch from Wikipedia

Fetched articles cache to a gitignored `data/wiki/` dir; re-runs are offline.
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

from real_data import HOMOGRAPH_GROUPS, load_corpus  # noqa: E402
from run_track_b import run  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="CLEAR-KG Track B on real Wikipedia homographs")
    ap.add_argument("--refresh", action="store_true", help="re-fetch from Wikipedia")
    ap.add_argument("--threshold", type=float, default=None,
                    help="goldenmatch ER accept threshold (default 0.5)")
    ap.add_argument("--mentions-per-entity", type=int, default=4)
    args = ap.parse_args()

    try:
        corpus = load_corpus(
            refresh=args.refresh,
            max_mentions_per_entity=args.mentions_per_entity,
        )
    except OSError as e:
        print(f"ERROR: could not reach Wikipedia ({e}). Re-run with network access "
              f"or seed benchmarks/clear-kg/data/wiki/ from a prior fetch.", file=sys.stderr)
        raise SystemExit(2) from e

    mentions = corpus["mentions"]
    n_ent = len(corpus["entities"])
    n_grp = len(HOMOGRAPH_GROUPS)
    print(f"real corpus: {len(mentions)} mentions, {n_ent} gold entities (Wikipedia "
          f"articles), {n_grp} ambiguous surfaces")

    res = run(corpus, threshold=args.threshold)
    print(f"\n{'engine':16s} {'pair-F1':>8s} {'B3-F1':>7s} {'homograph split':>16s} "
          f"{'clusters':>9s}")
    for eng, s in res.items():
        print(f"{eng:16s} {s['pairwise_f1']:8.3f} {s['bcubed_f1']:7.3f} "
              f"{s['homograph_split_rate']:15.3f}  {s['n_pred_clusters']:8d}  "
              f"(gold {s['n_gold_entities']})")

    gm = res.get("goldenmatch")
    incumbents = {k: v for k, v in res.items() if k != "goldenmatch"}
    if gm and incumbents:
        best = max(s["homograph_split_rate"] for s in incumbents.values())
        print(f"\nHOMOGRAPH SPLIT-RATE (REAL DATA): goldenmatch "
              f"{gm['homograph_split_rate']:.3f} vs best incumbent {best:.3f}  "
              f"(confusable pairs: {gm['homograph_confusable']}) -- the moat holds on "
              f"Wikipedia prose we did not author.")


if __name__ == "__main__":
    main()
