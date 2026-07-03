"""CLI: synthesis-given-gold (Stage 0 of the distillation pivot) -> SYNTHESIS_GOLD.md.

Needs the chat LLM (OPENAI_* env -> OpenAIClient; local Ollama or OpenAI). Wheel-free.

    python -m erkgbench.qa_e2e.run_synthesis_eval --n-questions 40 --out-md SYNTHESIS_GOLD.md
"""
from __future__ import annotations

import argparse
import os
import sys

from .synthesis_eval import evaluate_synthesis_given_gold, render_md


def _llm():
    from goldengraph.llm import OpenAIClient

    return OpenAIClient(model=os.environ.get("OPENAI_MODEL") or "gpt-4o-mini")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="synthesis-given-gold answer-match (isolation)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--n-questions", type=int, default=40)
    ap.add_argument("--ambiguity", type=float, default=0.6)
    ap.add_argument("--out-md", default="SYNTHESIS_GOLD.md")
    args = ap.parse_args(argv)

    res = evaluate_synthesis_given_gold(
        llm=_llm(), seed=args.seed, n_questions=args.n_questions, ambiguity=args.ambiguity
    )
    md = render_md(res, model=os.environ.get("OPENAI_MODEL") or "gpt-4o-mini")
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
