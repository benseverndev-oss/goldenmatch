"""Blind human labelling of real schemas, and scoring against those labels.

The two corpora that currently grade identity-layer detection were both built
inside this workstream. `layers_precision_corpus.json` is hand-authored by the
same person who tuned the detector; `layers_fk_schemas.json` removes the author
from the LABELLING but not from the choice of schemas, and its tables are
generated rather than found. Both now score at or near ceiling, which means
they have stopped discriminating.

This is the third instrument, and the only one whose labels come from outside:
**real column names, labelled by a human who has not seen what the detector
says about them.**

Two subcommands, deliberately separated so the labelling half cannot be
contaminated by the scoring half:

    python scripts/layers_blind_label.py worksheet      # emits schemas to label
    python scripts/layers_blind_label.py score          # scores once labels exist

`worksheet` NEVER imports infermap. That is enforced by a test rather than left
to discipline (`test_layers_blind_labels.py`), because a worksheet that leaked
predicted groupings would anchor the labeller and quietly turn this corpus into
another measure of self-agreement.

Scoring reuses the FK harness's partition metrics rather than reimplementing
them, so all three corpora are commensurable.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABELS = (
    ROOT / "packages" / "python" / "infermap" / "tests" / "fixtures"
    / "layers_blind_labels.json"
)

INSTRUCTIONS = [
    "HOW TO LABEL. For each schema below, group the columns by WHICH PARTY they",
    "describe, and give each party a short name of your choosing.",
    "",
    "A party is a real-world entity the table refers to -- a customer, a lender,",
    "a supplier, a device, a site. `lender_name` and `lender_id` are one party;",
    "`borrower_name` is another.",
    "",
    "Rules that matter for the result:",
    "  1. A single-entity table has exactly ONE party. That is the common case,",
    "     not a degenerate one -- do not feel obliged to find several.",
    "  2. Columns that describe no party (audit timestamps, lineage columns,",
    "     free-text notes, measures) go in `unassigned`. Leaving a column out is",
    "     a real answer, not an omission.",
    "  3. Name parties however you naturally would. Names are scored separately",
    "     from grouping and loosely; the GROUPING is the thing under test.",
    "  4. If a schema is unintelligible (mangled or coded column names), set",
    "     `skip: true` rather than guessing. A guess is worse than no label.",
    "",
    "Do NOT run the detector on these before labelling, and do not consult its",
    "output. The entire value of this corpus is that its labels are independent.",
]


def _harvest(from_dir: str | None = None) -> list[tuple[str, list[str]]]:
    """Distinct real CSV headers, diversity-deduplicated.

    Defaults to this repo, which is a WEAK sample by nature: it is a library
    repo of test fixtures, so its CSVs skew synthetic and benchmark-shaped.
    Point ``--from-dir`` at a directory of real schemas to get a corpus worth
    the labeller's time -- the instrument is only as good as what it samples.

    Deduped by TOKEN-SET SIMILARITY, not by exact header. The repo carries
    dozens of variants of a few benchmark datasets that differ in a column or
    two; deduping on anything narrower samples one dataset twenty-five times
    and calls it twenty-five schemas. A first attempt keyed on leading tokens
    did exactly that, which is why the bar is Jaccard overlap instead.
    """
    base = Path(from_dir).resolve() if from_dir else ROOT
    paths = subprocess.run(
        ["bash", "-c",
         "find . -name '*.csv' -not -path '*/node_modules/*' -not -path './.git/*' -size +100c"],
        capture_output=True, text=True, cwd=base,
    ).stdout.split()

    by_signature: dict[frozenset, tuple[str, list[str]]] = {}
    for p in sorted(paths):
        try:
            with open(base / p, newline="", encoding="utf-8", errors="replace") as fh:
                header = [h.strip() for h in next(csv.reader(fh)) if h.strip()]
        except Exception:
            continue
        if not (4 <= len(header) <= 40):
            continue
        by_signature[frozenset(_tokens_of(header))] = (p, header)

    # Greedy diversity filter: keep a schema only if it is meaningfully
    # different from everything already kept.
    kept: list[tuple[frozenset, str, list[str]]] = []
    for signature, (path, header) in sorted(by_signature.items(), key=lambda kv: kv[1][0]):
        if any(_jaccard(signature, prior) >= 0.5 for prior, _, _ in kept):
            continue
        kept.append((signature, path, header))
    return [(path, header) for _sig, path, header in kept]


def _tokens_of(header: list[str]) -> set[str]:
    out: set[str] = set()
    for col in header:
        for tok in col.lower().replace("-", "_").replace(" ", "_").split("_"):
            if tok:
                out.add(tok)
    return out


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _labelability(header: list[str]) -> float:
    """How much of a grouping QUESTION this schema actually poses a human.

    Two things make a schema worth a labeller's time, and both are needed:
    multi-token column names (a frame of single-token names has no grouping to
    find) and INTELLIGIBLE tokens. The repo is full of benchmark variants with
    deliberately mangled headers (`miller2_FiYe`, `assays_ASSI`) -- ranking on
    structure alone fills the worksheet with schemas whose only honest label is
    `skip`.
    """
    if not header:
        return 0.0
    multi = intelligible = 0
    for col in header:
        parts = [t for t in col.lower().replace("-", "_").replace(" ", "_").split("_") if t]
        if len(parts) > 1:
            multi += 1
        # A token of 4+ letters is probably a word rather than an abbreviation.
        if parts and sum(1 for t in parts if len(t) >= 4 and t.isalpha()) >= len(parts) / 2:
            intelligible += 1
    return (multi / len(header)) * (intelligible / len(header))


def _family(path: str) -> str:
    """Coarse provenance key, so one corner of the repo cannot fill the sheet."""
    return "/".join(path.split("/")[:4])


def build_worksheet(limit: int, from_dir: str | None = None) -> dict:
    harvested = _harvest(from_dir)
    ranked = sorted(harvested, key=lambda t: (-_labelability(t[1]), t[0]))

    # At most two per family: diversity of PROVENANCE on top of diversity of
    # token set, because near-identical generators live in the same directory.
    chosen: list[tuple[str, list[str]]] = []
    per_family: dict[str, int] = {}
    for path, header in ranked:
        fam = _family(path)
        if per_family.get(fam, 0) >= 2:
            continue
        per_family[fam] = per_family.get(fam, 0) + 1
        chosen.append((path, header))
        if len(chosen) >= limit:
            break

    return {
        "_instructions": INSTRUCTIONS,
        "_sampled_from": from_dir or "<repo>",
        "labelled_by": "",
        "schemas": [
            {
                "id": f"s{i:02d}",
                "source": path,
                "columns": header,
                "skip": False,
                # Fill in: {"party name": ["col", ...], ...}
                "parties": {},
                "unassigned": [],
            }
            for i, (path, header) in enumerate(chosen, 1)
        ],
    }


def _labelled(entry: dict) -> bool:
    return not entry.get("skip") and bool(entry.get("parties"))


def score() -> dict:
    """Score the detector against whatever has been labelled so far."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from types import SimpleNamespace

    from infermap import detect_identity_layers
    from layers_fk_groundtruth import _groups, _pairwise, _predicted_mapping

    spec = json.loads(LABELS.read_text(encoding="utf-8"))
    entries = spec["schemas"]
    done = [e for e in entries if _labelled(e)]

    cases = []
    for entry in done:
        columns = entry["columns"]
        truth: dict[str, str] = {}
        for party, cols in entry["parties"].items():
            for col in cols:
                truth[col] = party
        for col in columns:
            truth.setdefault(col, f"unassigned::{col}")

        result = detect_identity_layers(SimpleNamespace(columns=columns))
        pred = _predicted_mapping(result, columns)
        tp, pp, ap = _pairwise(truth, pred)
        precision = tp / pp if pp else 0.0
        recall = tp / ap if ap else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        cases.append({
            "id": entry["id"],
            "source": entry["source"],
            "true_parties": len(entry["parties"]),
            "got_parties": len(result.layers),
            "exact_partition": _groups(truth) == _groups(pred),
            "pairwise_f1": f1,
            "count_error": abs(len(result.layers) - len(entry["parties"])),
        })

    n = len(cases)
    return {
        "cases": cases,
        "summary": {
            "labelled": n,
            "total": len(entries),
            "skipped": sum(1 for e in entries if e.get("skip")),
            "exact_partition": (sum(c["exact_partition"] for c in cases) / n) if n else 0.0,
            "pairwise_f1": (sum(c["pairwise_f1"] for c in cases) / n) if n else 0.0,
            "mean_count_error": (sum(c["count_error"] for c in cases) / n) if n else 0.0,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("worksheet", help="emit schemas to label (never runs the detector)")
    w.add_argument("--limit", type=int, default=25)
    w.add_argument("--from-dir", default=None,
                   help="sample real schemas from this directory instead of the repo")
    w.add_argument("--force", action="store_true", help="overwrite existing labels")
    sub.add_parser("score", help="score the detector against submitted labels")
    args = ap.parse_args()

    if args.cmd == "worksheet":
        if LABELS.exists() and not args.force:
            existing = json.loads(LABELS.read_text(encoding="utf-8"))
            if any(_labelled(e) for e in existing["schemas"]):
                print(f"refusing to overwrite {LABELS.name}: it already carries labels "
                      f"(--force to override)", file=sys.stderr)
                return 1
        LABELS.write_text(
            json.dumps(build_worksheet(args.limit, args.from_dir), indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"wrote worksheet -> {LABELS}")
        print("Fill in `parties` per schema, then: python scripts/layers_blind_label.py score")
        return 0

    res = score()
    s = res["summary"]
    if not s["labelled"]:
        print("No schemas labelled yet.")
        print(f"  {s['total']} awaiting labels in {LABELS.name}"
              f" ({s['skipped']} marked skip)")
        print("  See the `_instructions` block in that file.")
        return 0

    print("=" * 72)
    print("IDENTITY LAYERS vs BLIND HUMAN LABELS")
    print("=" * 72)
    for c in res["cases"]:
        flag = "ok  " if c["exact_partition"] else "MISS"
        print(f"  {flag} {c['id']}  {c['source'][-40:]:42} "
              f"want {c['true_parties']} got {c['got_parties']}  F1={c['pairwise_f1']:.2f}")
    print("-" * 72)
    print(f"  labelled {s['labelled']}/{s['total']}  ({s['skipped']} skipped)")
    print(f"  exact partition   {s['exact_partition']:.0%}")
    print(f"  pairwise F1       {s['pairwise_f1']:.2f}")
    print(f"  mean count error  {s['mean_count_error']:.2f}")
    print("-" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
