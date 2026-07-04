"""CLEAR-KG Track A — real-prose extraction number on Re-DocRED.

    OPENAI_API_KEY=... python benchmarks/clear-kg/run_redocred.py --docs 25

Loads a slice of the Re-DocRED dev set (real Wikipedia + gold triples), runs an
LLM relation extractor at its documented zero-shot default, and reports
micro relation-F1 vs the published Re-DocRED numbers. Needs a key + network; the
harness itself is unit-tested offline with a mock extractor.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from llm_extractor import mock_extract, openai_extract  # noqa: E402
from redocred import load_docs  # noqa: E402
from score_redocred import score_redocred  # noqa: E402

_SOTA = "Re-DocRED relation-F1 reference: ~80.7 (fine-tuned BERT/DREEAM) / ~74.6 (strong LLM)"


def run(docs: list[dict], schema: list[str], *, model: str, extractor=openai_extract,
        progress=False) -> dict:
    preds = []
    for i, d in enumerate(docs):
        preds.append(extractor(d, schema, model=model) if extractor is openai_extract
                     else extractor(d, schema))
        if progress:
            print(f"  [{i+1}/{len(docs)}] {d['title'][:48]}", file=sys.stderr)
    return score_redocred(preds, docs)


def main():
    ap = argparse.ArgumentParser(description="CLEAR-KG Track A on Re-DocRED (real prose)")
    ap.add_argument("--docs", type=int, default=25)
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--mock", action="store_true", help="offline dry-run (no key/network)")
    args = ap.parse_args()

    if not args.mock and not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: set OPENAI_API_KEY (or pass --mock for an offline dry-run).",
              file=sys.stderr)
        raise SystemExit(2)

    docs, schema = load_docs(limit=args.docs)
    print(f"Re-DocRED: {len(docs)} docs, {sum(len(d['gold']) for d in docs)} gold triples, "
          f"{len(schema)}-relation closed schema")

    extractor = mock_extract if args.mock else openai_extract
    s = run(docs, schema, model=args.model, extractor=extractor, progress=True)
    print(f"\nmodel: {'mock (oracle)' if args.mock else args.model}")
    print(f"micro P {s['precision']:.3f}  R {s['recall']:.3f}  F1 {s['f1']:.3f}  "
          f"(tp {s['tp']} / pred {s['n_pred']} / gold {s['n_gold']})")
    print(f"\n{_SOTA}")
    print("Track A is table stakes: extraction is LLM-bound; this measures where a "
          "zero-shot extractor lands on the real standard benchmark, so the ER + "
          "faithfulness wins are not 'good at the easy part' unmeasured.")


if __name__ == "__main__":
    main()
