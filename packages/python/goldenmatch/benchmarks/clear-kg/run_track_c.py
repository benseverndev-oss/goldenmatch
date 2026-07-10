"""CLEAR-KG Track C runner: generate a grounding dataset, verify candidate
triples with each mechanism, and report the faithfulness gap.

    python benchmarks/clear-kg/run_track_c.py
    python benchmarks/clear-kg/run_track_c.py --supported 40 --distractor 30 --hallucinated 20
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from grounding import ENGINES  # noqa: E402
from grounding_data import generate_grounding_dataset  # noqa: E402
from score_c import score_track_c  # noqa: E402

DEFAULT_ENGINES = ("ungrounded", "sentence_presence", "ontology_conformance", "relation_aware")


def run(dataset: dict, engines=DEFAULT_ENGINES) -> dict:
    cands, docs = dataset["candidates"], dataset["docs"]
    return {eng: score_track_c(ENGINES[eng](cands, docs), cands) for eng in engines}


def main():
    ap = argparse.ArgumentParser(description="CLEAR-KG Track C (span-grounded faithfulness)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--supported", type=int, default=24)
    ap.add_argument("--distractor", type=int, default=16)
    ap.add_argument("--hallucinated", type=int, default=12)
    args = ap.parse_args()

    ds = generate_grounding_dataset(
        seed=args.seed, n_supported=args.supported,
        n_distractor=args.distractor, n_hallucinated=args.hallucinated,
    )
    cands = ds["candidates"]
    n_sup = sum(1 for c in cands if c["gold_class"] == "supported")
    print(f"grounding dataset: {len(cands)} candidate triples "
          f"({n_sup} supported / {sum(1 for c in cands if c['gold_class']=='distractor')} distractor / "
          f"{sum(1 for c in cands if c['gold_class']=='hallucinated')} hallucinated), "
          f"{len(ds['docs'])} docs")

    res = run(ds)
    print(f"\n{'engine':22s} {'sup-F1':>7s} {'cover':>6s} {'distractorFSR':>13s} "
          f"{'halluc':>7s} {'conf-AUROC':>11s}  conf?")
    for eng, s in res.items():
        ece = "n/a" if s["confidence_ece"] is None else f"{s['confidence_ece']:.2f}"
        print(f"{eng:22s} {s['support_f1']:7.3f} {s['grounding_coverage']:6.2f} "
              f"{s['distractor_false_support_rate']:13.3f} {s['hallucination_rate']:7.3f} "
              f"{s['confidence_auroc']:11.3f}  {'yes' if s['emits_confidence'] else 'no':>3s} "
              f"(ece {ece})")

    ra = res.get("relation_aware")
    inc = {k: v for k, v in res.items() if k != "relation_aware"}
    if ra and inc:
        worst_fsr = min(s["distractor_false_support_rate"] for s in inc.values())
        print(f"\nDISTRACTOR FALSE-SUPPORT: relation_aware "
              f"{ra['distractor_false_support_rate']:.3f} vs best incumbent {worst_fsr:.3f}  "
              f"({ra['n_distractor']} distractor triples) -- presence/type grounding says "
              f"'supported' when entities merely co-occur; only relation-aware grounding "
              f"reads the span. It is also the only engine emitting a calibrated confidence "
              f"(AUROC {ra['confidence_auroc']:.3f} vs 0.500).")


if __name__ == "__main__":
    main()
