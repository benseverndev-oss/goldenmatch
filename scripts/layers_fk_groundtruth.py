"""Score identity-layer detection against MECHANICALLY-DERIVED ground truth.

The hand-authored precision corpus (`layers_precision_corpus.json`) has a
weakness it cannot fix on its own: its labels were written by the same person
tuning the detector, so a perfect score there partly measures self-agreement.

This harness removes the author from the labelling loop. It takes normalized
schemas (`layers_fk_schemas.json`), denormalizes each by joining a fact to its
dimensions, and derives every label mechanically: **a column's party is the
table it came from.** The grouping is decided by the schema's original
designer; this script only decides how columns are renamed on the way out.

Renaming conventions are the variable under test, because the affix signal is
what detection rests on. `initial` (TPC-H's own `c_name`/`s_name` style) is
included precisely because it is expected to FAIL -- a one-character qualifier
is below the kernel's minimum, so that schema cannot be segmented by name at
all. Reporting a known blind spot is worth more than choosing conventions the
detector wins on.

Metrics, in increasing strictness:

* **pairwise F1** -- over all column pairs, do the two columns belong to the
  same party? Partial credit, so a detector that gets most of a partition right
  is distinguishable from one that gets none of it.
* **exact partition** -- the predicted set of column-groups equals the true
  one. Unforgiving, and the honest headline.
* **party-count error** -- |predicted - true|, the quantity per-block config
  (#2575) would partition on.

Unassigned columns are counted as their own singleton groups: refusing to place
a column is a real answer with real consequences, not an abstention to exclude.

    python scripts/layers_fk_groundtruth.py
    python scripts/layers_fk_groundtruth.py --json
"""
from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = (
    ROOT / "packages" / "python" / "infermap" / "tests" / "fixtures"
    / "layers_fk_schemas.json"
)


def _rename(entity: str, column: str, conv: dict) -> str:
    qualifier = entity[: conv["abbrev"]] if conv["abbrev"] else entity
    if conv["style"] == "suffix":
        return f"{column}_{qualifier}"
    return f"{qualifier}_{column}"


def denormalize(schema: dict, conv: dict) -> tuple[list[str], dict[str, str]]:
    """Return (columns, column -> true party).

    The fact's own columns form their own party, named for the fact entity --
    an orders table joined to customer and supplier describes three
    populations, not two.
    """
    columns: list[str] = []
    truth: dict[str, str] = {}
    for col in schema["own_columns"]:
        name = _rename(schema["fact"], col, conv)
        columns.append(name)
        truth[name] = schema["fact"]
    for entity, cols in schema["dimensions"].items():
        for col in cols:
            name = _rename(entity, col, conv)
            columns.append(name)
            truth[name] = entity
    return columns, truth


def _groups(mapping: dict[str, str]) -> set[frozenset[str]]:
    out: dict[str, set[str]] = {}
    for col, party in mapping.items():
        out.setdefault(party, set()).add(col)
    return {frozenset(v) for v in out.values()}


def _predicted_mapping(result, columns: list[str]) -> dict[str, str]:
    """Predicted column -> party id. Unassigned columns become singletons."""
    mapping: dict[str, str] = {}
    for i, layer in enumerate(result.layers):
        for col in layer.columns:
            mapping.setdefault(col, f"layer{i}")
    for col in columns:
        mapping.setdefault(col, f"unassigned::{col}")
    return mapping


def _pairwise(truth: dict[str, str], pred: dict[str, str]) -> tuple[int, int, int]:
    """(true positives, predicted positives, actual positives) over column pairs."""
    tp = pp = ap = 0
    for a, b in combinations(sorted(truth), 2):
        same_true = truth[a] == truth[b]
        same_pred = pred[a] == pred[b]
        if same_pred:
            pp += 1
        if same_true:
            ap += 1
        if same_true and same_pred:
            tp += 1
    return tp, pp, ap


def run(detect) -> dict:
    spec = json.loads(SCHEMAS.read_text(encoding="utf-8"))
    conventions = {k: v for k, v in spec["conventions"].items() if not k.startswith("_")}
    cases = []

    for schema in spec["schemas"]:
        for conv_name, conv in conventions.items():
            columns, truth = denormalize(schema, conv)
            result = detect(SimpleNamespace(columns=columns))
            pred = _predicted_mapping(result, columns)
            tp, pp, ap = _pairwise(truth, pred)
            precision = tp / pp if pp else 0.0
            recall = tp / ap if ap else 0.0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
            true_parties = len(set(truth.values()))
            cases.append({
                "schema": schema["name"],
                "source": schema["source"],
                "convention": conv_name,
                "detectable": conv["detectable"],
                "true_parties": true_parties,
                "got_parties": len(result.layers),
                "exact_partition": _groups(truth) == _groups(pred),
                "pairwise_precision": precision,
                "pairwise_recall": recall,
                "pairwise_f1": f1,
                "count_error": abs(len(result.layers) - true_parties),
            })

    detectable = [c for c in cases if c["detectable"]]
    blind = [c for c in cases if not c["detectable"]]

    def _agg(rows):
        if not rows:
            return {}
        return {
            "n": len(rows),
            "exact_partition": sum(r["exact_partition"] for r in rows) / len(rows),
            "pairwise_f1": sum(r["pairwise_f1"] for r in rows) / len(rows),
            "pairwise_precision": sum(r["pairwise_precision"] for r in rows) / len(rows),
            "pairwise_recall": sum(r["pairwise_recall"] for r in rows) / len(rows),
            "mean_count_error": sum(r["count_error"] for r in rows) / len(rows),
        }

    by_conv = {}
    for conv_name in conventions:
        by_conv[conv_name] = _agg([c for c in cases if c["convention"] == conv_name])

    return {
        "cases": cases,
        "summary": {
            "detectable": _agg(detectable),
            "known_blind_spot": _agg(blind),
            "by_convention": by_conv,
        },
    }


def _report(res: dict) -> None:
    s = res["summary"]
    print("=" * 76)
    print("IDENTITY LAYERS vs FOREIGN-KEY GROUND TRUTH")
    print("  labels derived mechanically: a column's party is the table it came from")
    print("=" * 76)

    print("\nBY RENAMING CONVENTION")
    print(f"  {'convention':12} {'n':>3}  {'exact':>7} {'pair-F1':>8} {'pair-P':>7} {'pair-R':>7} {'cnt err':>8}")
    for name, a in s["by_convention"].items():
        if not a:
            continue
        print(f"  {name:12} {a['n']:3}  {a['exact_partition']:6.0%} {a['pairwise_f1']:8.2f} "
              f"{a['pairwise_precision']:7.2f} {a['pairwise_recall']:7.2f} {a['mean_count_error']:8.2f}")

    print("\nPER CASE (detectable conventions only)")
    for c in res["cases"]:
        if not c["detectable"]:
            continue
        flag = "ok  " if c["exact_partition"] else "MISS"
        print(f"  {flag} {c['schema']:22} {c['convention']:8} "
              f"want {c['true_parties']} got {c['got_parties']}  F1={c['pairwise_f1']:.2f}")

    d, b = s["detectable"], s["known_blind_spot"]
    print("\n" + "-" * 76)
    print(f"  DETECTABLE conventions   exact {d['exact_partition']:.0%}   "
          f"pairwise F1 {d['pairwise_f1']:.2f}   mean count error {d['mean_count_error']:.2f}")
    if b:
        print(f"  KNOWN BLIND SPOT         exact {b['exact_partition']:.0%}   "
              f"pairwise F1 {b['pairwise_f1']:.2f}   (single-char qualifiers)")
    print("-" * 76)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    from infermap import detect_identity_layers

    res = run(detect_identity_layers)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        _report(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
