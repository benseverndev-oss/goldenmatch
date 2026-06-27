"""CLI: run the real-LLM scorecard rows (extraction-F1, synthesis-given-gold,
4-dial answer-match ablation), write SCORECARD.md. Opt-in, budget-capped, NON-gating
-- always exits 0. Needs OPENAI_API_KEY + the goldengraph_native wheel.

Example:
    python -m erkgbench.qa_e2e.run_scorecard --seed 7 --n-questions 60 \
        --ambiguity 0.6 --budget-usd 2 --out-md SCORECARD.md
"""
from __future__ import annotations

import argparse
import os
import sys


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="GoldenGraph real-LLM scorecard")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--n-questions", type=int, default=60)
    p.add_argument("--ambiguity", type=float, default=0.6)
    p.add_argument("--max-hops", type=int, default=4)
    p.add_argument("--budget-usd", type=float, default=2.0)
    p.add_argument("--out-md", default="SCORECARD.md")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if not os.environ.get("OPENAI_API_KEY"):
        sys.stderr.write("No OPENAI_API_KEY; skipping the scorecard (opt-in lane).\n")
        return 0

    from goldengraph.llm import OpenAIClient

    from .scorecard_llm import render_scorecard_md, run_scorecard

    res = run_scorecard(
        seed=args.seed,
        n_questions=args.n_questions,
        ambiguity=args.ambiguity,
        max_hops=args.max_hops,
        inner_llm=OpenAIClient(model="gpt-4o-mini"),
        budget_usd=args.budget_usd,
    )
    md = render_scorecard_md(res)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    sys.stdout.write(md)
    return 0  # non-gating


if __name__ == "__main__":
    raise SystemExit(main())
