"""CLI: retrieval coverage on the oracle graph (Stage 0b) -> RETRIEVAL_COVERAGE.md.

Needs the wheel (PyStore) + the embedder (OPENAI_EMBED_MODEL via Ollama for local, else OpenAI).

    python -m erkgbench.qa_e2e.run_retrieval_eval --n-questions 40 --out-md RETRIEVAL_COVERAGE.md
"""
from __future__ import annotations

import argparse
import os
import sys

from .retrieval_eval import evaluate_retrieval_coverage, render_md


def _embedder():
    from goldengraph.embed import GoldenmatchEmbedder

    return GoldenmatchEmbedder(provider="openai", model=os.environ.get("OPENAI_EMBED_MODEL") or None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="retrieval coverage of the gold chain (isolation)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--n-questions", type=int, default=40)
    ap.add_argument("--ambiguity", type=float, default=0.6)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--hops", type=int, default=6)
    ap.add_argument("--node-budget", type=int, default=256)
    ap.add_argument("--out-md", default="RETRIEVAL_COVERAGE.md")
    args = ap.parse_args(argv)

    res = evaluate_retrieval_coverage(
        embedder=_embedder(), seed=args.seed, n_questions=args.n_questions,
        ambiguity=args.ambiguity, k=args.k, hops=args.hops, node_budget=args.node_budget,
    )
    md = render_md(res, embed_model=os.environ.get("OPENAI_EMBED_MODEL") or "openai-default")
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
