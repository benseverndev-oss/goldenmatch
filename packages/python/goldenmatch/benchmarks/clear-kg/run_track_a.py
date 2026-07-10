"""CLEAR-KG Track A runner: extract triples, then score them under each
canonicalization mode -- showing the alias penalty (exact vs relaxed) and the
homograph mis-credit that only ER-aware matching avoids (relaxed vs er_aware).

    python benchmarks/clear-kg/run_track_a.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from extract_data import generate_extraction_corpus  # noqa: E402
from extract_score import score_extraction  # noqa: E402
from extractors import EXTRACTORS  # noqa: E402

MODES = ("exact", "relaxed", "er_aware")


def run(dataset: dict, extractors=("pattern", "lossy")) -> dict:
    out = {}
    for name in extractors:
        preds = EXTRACTORS[name](dataset)
        out[name] = {m: score_extraction(preds, dataset, m) for m in MODES}
    return out


def main():
    ap = argparse.ArgumentParser(description="CLEAR-KG Track A (extraction triple-F1)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--persons", type=int, default=8)
    ap.add_argument("--homograph-pairs", type=int, default=2)
    args = ap.parse_args()

    ds = generate_extraction_corpus(seed=args.seed, n_persons=args.persons,
                                    n_homograph_pairs=args.homograph_pairs)
    print(f"extraction corpus: {len(ds['gold'])} gold triples, {len(ds['docs'])} docs, "
          f"{len(ds['homograph_ids'])} homograph subjects")

    res = run(ds)
    for name, modes in res.items():
        print(f"\n[{name} extractor]  {'mode':10s} {'P':>6s} {'R':>6s} {'F1':>6s} "
              f"{'homograph-R':>12s}")
        for m in MODES:
            s = modes[m]
            print(f"{'':22s} {m:10s} {s['precision']:6.3f} {s['recall']:6.3f} "
                  f"{s['f1']:6.3f} {s['homograph_recall']:12.3f}")

    p = res["pattern"]
    print(f"\nEXTRACTION-F1 (pattern extractor): exact {p['exact']['f1']:.3f} "
          f"-> relaxed {p['relaxed']['f1']:.3f} -> er_aware {p['er_aware']['f1']:.3f}. "
          f"exact under-counts (alias canonicalization penalty); relaxed mis-credits "
          f"homographs (homograph-recall {p['relaxed']['homograph_recall']:.3f} vs "
          f"{p['er_aware']['homograph_recall']:.3f}); ER-aware canonicalization scores "
          f"correctly. Even the extraction metric inherits the moat.")


if __name__ == "__main__":
    main()
