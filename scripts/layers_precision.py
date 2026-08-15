"""Measure identity-layer detection PRECISION against a convention-labelled corpus.

The corpus in `test_layers.py` was authored alongside the detector's semantics,
so it cannot answer "is this right" -- it encodes the same assumptions it tests.
This harness scores the detector against
`packages/python/infermap/tests/fixtures/layers_precision_corpus.json`, whose
labels come from the NAMING CONVENTION each schema uses rather than from how the
detector happens to behave.

Two halves, and both are load-bearing:

* **Negatives** are single-entity tables whose columns share a qualifier for a
  reason that is not a party (warehouse lineage, the table's own name, audit
  trails, units). Correct output is one party. This measures the false-party
  rate -- the failure that would make per-block configuration (#2575) partition
  on a segmentation that does not exist.
* **Positives** are genuinely multi-party tables. Without them a detector that
  never splits anything scores a perfect specificity, so the two numbers are
  only meaningful reported together.

Grouping is scored as a PARTITION, not just a count, because the partition is
what a downstream consumer acts on. A run that gets the party count right by
grouping the wrong columns together is not a pass.

    python scripts/layers_precision.py            # full report
    python scripts/layers_precision.py --json     # machine-readable

Exits non-zero only with --check plus a threshold that is not met, so the
default run is a measurement rather than a gate.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
CORPUS = (
    ROOT / "packages" / "python" / "infermap" / "tests" / "fixtures"
    / "layers_precision_corpus.json"
)


def _frame(columns: list[str]):
    """Detection reads ``df.columns`` and nothing else."""
    return SimpleNamespace(columns=columns)


def _partition(layers) -> list[frozenset[str]]:
    return [frozenset(layer.columns) for layer in layers]


def _groups_match(predicted, expected: list[list[str]]) -> bool:
    """Exact partition equality over the columns the label names.

    Compared as a set of frozensets so layer order and column order within a
    layer are both irrelevant -- neither carries meaning.
    """
    return set(predicted) == {frozenset(g) for g in expected}


def run(detect) -> dict:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    results: dict = {"negatives": [], "positives": []}

    for case in corpus["negatives"]:
        result = detect(_frame(case["columns"]), domain=case.get("domain"))
        n = len(result.layers)
        results["negatives"].append({
            "name": case["name"],
            "class": case["class"],
            "expected_parties": case["expected_parties"],
            "got_parties": n,
            "ok": n <= case["expected_parties"],
            "roles": [lyr.role for lyr in result.layers],
            "groups": [sorted(lyr.columns) for lyr in result.layers],
        })

    for case in corpus["positives"]:
        result = detect(_frame(case["columns"]), domain=case.get("domain"))
        predicted = _partition(result.layers)
        count_ok = len(result.layers) == case["expected_parties"]
        groups_ok = _groups_match(predicted, case["expected_groups"])
        results["positives"].append({
            "name": case["name"],
            "class": case["class"],
            "expected_parties": case["expected_parties"],
            "got_parties": len(result.layers),
            "count_ok": count_ok,
            "groups_ok": groups_ok,
            "roles": [lyr.role for lyr in result.layers],
            "groups": [sorted(lyr.columns) for lyr in result.layers],
        })

    neg = results["negatives"]
    pos = results["positives"]
    results["summary"] = {
        "specificity": sum(c["ok"] for c in neg) / len(neg) if neg else 0.0,
        "false_party_cases": [c["name"] for c in neg if not c["ok"]],
        "sensitivity_count": sum(c["count_ok"] for c in pos) / len(pos) if pos else 0.0,
        "sensitivity_partition": sum(c["groups_ok"] for c in pos) / len(pos) if pos else 0.0,
        "wrong_partition_cases": [c["name"] for c in pos if not c["groups_ok"]],
        "n_negatives": len(neg),
        "n_positives": len(pos),
    }
    return results


def _report(res: dict) -> None:
    s = res["summary"]
    print("=" * 72)
    print("IDENTITY-LAYER PRECISION")
    print("=" * 72)

    print(f"\nNEGATIVES ({s['n_negatives']}) -- single-entity tables; >1 party is a false split")
    by_class: dict[str, list] = {}
    for c in res["negatives"]:
        by_class.setdefault(c["class"], []).append(c)
    for cls, cases in sorted(by_class.items()):
        ok = sum(c["ok"] for c in cases)
        print(f"  {cls:16} {ok}/{len(cases)}")
        for c in cases:
            if not c["ok"]:
                print(f"      FALSE SPLIT  {c['name']}: {c['got_parties']} parties {c['groups']}")

    print(f"\nPOSITIVES ({s['n_positives']}) -- genuinely multi-party; guards against never-splitting")
    for c in res["positives"]:
        flag = "ok  " if c["groups_ok"] else "MISS"
        print(f"  {flag} {c['name']:32} want {c['expected_parties']} got {c['got_parties']}")
        if not c["groups_ok"]:
            print(f"        groups: {c['groups']}")

    print("\n" + "-" * 72)
    print(f"  specificity  (no false party)      {s['specificity']:.0%}")
    print(f"  sensitivity  (party count right)   {s['sensitivity_count']:.0%}")
    print(f"  sensitivity  (partition exact)     {s['sensitivity_partition']:.0%}")
    print("-" * 72)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--check", action="store_true", help="exit non-zero below thresholds")
    ap.add_argument("--min-specificity", type=float, default=0.0)
    ap.add_argument("--min-sensitivity", type=float, default=0.0)
    args = ap.parse_args()

    from infermap import detect_identity_layers

    res = run(detect_identity_layers)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        _report(res)

    if args.check:
        s = res["summary"]
        bad = []
        if s["specificity"] < args.min_specificity:
            bad.append(f"specificity {s['specificity']:.0%} < {args.min_specificity:.0%}")
        if s["sensitivity_partition"] < args.min_sensitivity:
            bad.append(f"partition sensitivity {s['sensitivity_partition']:.0%} < {args.min_sensitivity:.0%}")
        if bad:
            print("\nFAIL: " + "; ".join(bad), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
