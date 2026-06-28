"""Stage 1: turn a goldengraph DISTILL_LOG into a clean teacher-label dataset.

A teacher run (gpt-4o-mini) with `GOLDENGRAPH_DISTILL_LOG=<path>` already appends one JSONL record per
document: {text, entities:[{name,type,context}], relationships:[{subj,predicate,obj}], attributes, ...}
(see goldengraph.ingest._DistillLogger). This script validates + filters those into `pairs.jsonl`:
drops records whose extraction is empty (no usable supervision) and de-dupes identical texts.

Usage:
    python scripts/distill/capture_pairs.py --in distill.jsonl --out scripts/distill/data/pairs.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys


def clean_pairs(records):
    """Yield (text, record) for records with a non-empty extraction, de-duped by text."""
    seen = set()
    for rec in records:
        text = (rec.get("text") or "").strip()
        if not text or text in seen:
            continue
        ents = rec.get("entities") or []
        rels = rec.get("relationships") or []
        # A usable supervision example needs at least one entity AND one relationship (a triple to
        # learn). Entity-only or relation-less docs teach the student nothing about edges.
        if not ents or not rels:
            continue
        seen.add(text)
        yield {
            "text": text,
            "entities": ents,
            "relationships": rels,
            "attributes": rec.get("attributes") or [],
        }


def _read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="DISTILL_LOG -> clean teacher pairs.jsonl")
    ap.add_argument("--in", dest="inp", required=True, help="the GOLDENGRAPH_DISTILL_LOG jsonl")
    ap.add_argument("--out", default="scripts/distill/data/pairs.jsonl")
    args = ap.parse_args(argv)

    import os

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    n_in = n_out = 0
    with open(args.out, "w", encoding="utf-8") as out:
        for rec in clean_pairs(_read_jsonl(args.inp)):
            n_in += 1  # noqa: SIM113 (count of yielded == kept)
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n_out += 1
    sys.stdout.write(f"kept {n_out} usable pairs -> {args.out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
