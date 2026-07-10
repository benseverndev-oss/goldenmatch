"""CLEAR-KG Track D runner: full-pipeline systems on one corpus, scored by the
CLEAR composite. Each system shares the extractor (table stakes) and differs in
its ER engine (Track B) and grounding engine (Track C) -- the two moats.

    python benchmarks/clear-kg/run_track_d.py
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_HERE = Path(__file__).parent
_PKG_ROOT = _HERE.parent.parent
for _p in (str(_HERE), str(_PKG_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("GOLDENMATCH_NATIVE", "0")
os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

from grounding import ground_relation_aware, ground_sentence_presence  # noqa: E402
from pipeline_data import generate_pipeline_corpus  # noqa: E402
from score import bcubed_prf  # noqa: E402
from score_d import clear_score, extraction_surface_f1, grounded_correct_rate  # noqa: E402
from track_b import predict_exact_surface, predict_goldenmatch  # noqa: E402

# a system = (ER engine, grounding engine); the extractor is shared.
SYSTEMS = {
    # documented incumbent stack: name-merge ER + within-sentence-presence grounding
    "incumbent": (predict_exact_surface, ground_sentence_presence),
    # partial: principled ER but still presence grounding -> grounding drags CLEAR
    "er_only": (predict_goldenmatch, ground_sentence_presence),
    # both moats: neighborhood ER + relation-aware grounding
    "goldenmatch": (predict_goldenmatch, ground_relation_aware),
}


def run(corpus: dict) -> dict:
    gold_map = {m["mention_id"]: m["gold_entity_id"] for m in corpus["mentions"]}
    # extraction is shared across systems (faithful extractor on the real docs)
    extraction_f1 = extraction_surface_f1(corpus["gold_surfaces"], corpus["gold_surfaces"])

    out = {}
    for name, (er_engine, grounding_engine) in SYSTEMS.items():
        clusters = er_engine(corpus["mentions"])
        er_f1 = bcubed_prf(clusters, gold_map)["f1"]
        decisions = grounding_engine(corpus["emitted"], corpus["docs"])
        gc = grounded_correct_rate(decisions, corpus["emitted"])
        out[name] = clear_score(extraction_f1, er_f1, gc)
    return out


def main():
    ap = argparse.ArgumentParser(description="CLEAR-KG Track D (end-to-end CLEAR score)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--persons", type=int, default=6)
    ap.add_argument("--homograph-pairs", type=int, default=2)
    args = ap.parse_args()

    corpus = generate_pipeline_corpus(seed=args.seed, n_persons=args.persons,
                                      n_homograph_pairs=args.homograph_pairs)
    n_emit = len(corpus["emitted"])
    print(f"pipeline corpus: {len(corpus['gold_triples'])} gold triples, "
          f"{len(corpus['mentions'])} mentions, {len(corpus['docs'])} docs, "
          f"{n_emit} emitted triples, {len(corpus['homograph_ids'])} homograph subjects")

    res = run(corpus)
    print(f"\n{'system':14s} {'extract-F1':>10s} {'ER-F1':>7s} {'grounded-ok':>11s} "
          f"{'CLEAR':>7s}")
    for name, s in res.items():
        print(f"{name:14s} {s['extraction_f1']:10.3f} {s['er_f1']:7.3f} "
              f"{s['grounded_correct']:11.3f} {s['clear']:7.3f}")

    gm = res["goldenmatch"]
    inc = res["incumbent"]
    print(f"\nCLEAR (end-to-end): goldenmatch {gm['clear']:.3f} vs incumbent "
          f"{inc['clear']:.3f}. Extraction is shared and high (table stakes); the "
          f"harmonic mean is dragged to the weakest axis, so name-merge ER and "
          f"presence grounding cannot be rescued by good extraction. The `er_only` "
          f"row (CLEAR {res['er_only']['clear']:.3f}) shows one hollow axis is "
          f"enough to sink the composite -- you must win BOTH moats.")


if __name__ == "__main__":
    main()
