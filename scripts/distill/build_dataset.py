"""Stage 2: split clean teacher pairs into train/val/heldout with DOCUMENT-disjoint partitions.

Disjoint-by-document is mandatory: the same text must never appear in two splits, or extraction-F1 on
the heldout is leaked. Deterministic (hash of text) so re-runs are stable. Also emits a schema report
(predicate vocabulary + entity-type coverage) so a teacher/REBEL vocab mismatch is visible up front.

Usage:
    python scripts/distill/build_dataset.py --in scripts/distill/data/pairs.jsonl \
        --out-dir scripts/distill/data/dataset --val 0.1 --heldout 0.1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter


def _bucket(text: str) -> float:
    """Stable [0,1) hash of the document text -> deterministic split assignment (no RNG)."""
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def split_records(records, *, val: float, heldout: float):
    """Partition by document hash: [0,heldout)=heldout, [heldout,heldout+val)=val, rest=train."""
    train, va, held = [], [], []
    for rec in records:
        b = _bucket(rec["text"])
        if b < heldout:
            held.append(rec)
        elif b < heldout + val:
            va.append(rec)
        else:
            train.append(rec)
    return train, va, held


def schema_report(records) -> dict:
    preds: Counter = Counter()
    types: Counter = Counter()
    for rec in records:
        for r in rec.get("relationships", []):
            preds[str(r.get("predicate", ""))] += 1
        for e in rec.get("entities", []):
            types[str(e.get("type", ""))] += 1
    return {"predicates": dict(preds.most_common()), "entity_types": dict(types.most_common())}


def _read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _write_jsonl(path, recs):
    with open(path, "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="pairs.jsonl -> train/val/heldout (document-disjoint)")
    ap.add_argument("--in", dest="inp", default="scripts/distill/data/pairs.jsonl")
    ap.add_argument("--out-dir", default="scripts/distill/data/dataset")
    ap.add_argument("--val", type=float, default=0.1)
    ap.add_argument("--heldout", type=float, default=0.1)
    args = ap.parse_args(argv)

    recs = _read_jsonl(args.inp)
    train, va, held = split_records(recs, val=args.val, heldout=args.heldout)
    os.makedirs(args.out_dir, exist_ok=True)
    _write_jsonl(os.path.join(args.out_dir, "train.jsonl"), train)
    _write_jsonl(os.path.join(args.out_dir, "val.jsonl"), va)
    _write_jsonl(os.path.join(args.out_dir, "heldout.jsonl"), held)
    report = schema_report(recs)
    with open(os.path.join(args.out_dir, "schema_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print(
        f"train={len(train)} val={len(va)} heldout={len(held)} | "
        f"{len(report['predicates'])} predicates, {len(report['entity_types'])} entity-types "
        f"-> {args.out_dir}/"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
