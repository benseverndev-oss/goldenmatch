"""CLI: run the deterministic temporal as_of capability bench (goldengraph
store.as_of vs a temporal-blind floor), write TEMPORAL.md, exit non-zero on a HARD
gate failure. Key-free; needs the goldengraph_native wheel.

Example:
    python -m erkgbench.qa_e2e.run_temporal --seed 7 --n-facts 40 \
        --ambiguity 0.6 --out-md TEMPORAL.md
"""
from __future__ import annotations

import argparse
import os
import sys

from .temporal import gate_exit_code, render_temporal_md, run_temporal_deterministic


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="GoldenGraph temporal as_of capability bench")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--n-facts", type=int, default=40)
    p.add_argument("--ambiguity", type=float, default=0.6)
    p.add_argument("--out-md", default="TEMPORAL.md")
    p.add_argument("--with-llm", action="store_true",
                   help="also score the realistic real-LLM RAG floor (needs OPENAI_API_KEY)")
    p.add_argument("--budget-usd", type=float, default=2.0)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    llm = None
    if args.with_llm and os.environ.get("OPENAI_API_KEY"):
        from goldengraph.llm import OpenAIClient
        from goldenmatch.config.schemas import BudgetConfig
        from goldenmatch.core.llm_budget import BudgetTracker

        from .scorecard_llm import _BudgetedLLM

        llm = _BudgetedLLM(
            OpenAIClient(model="gpt-4o-mini"),
            BudgetTracker(BudgetConfig(max_cost_usd=args.budget_usd)),
        )
    res = run_temporal_deterministic(
        seed=args.seed, n_facts=args.n_facts, ambiguity=args.ambiguity, llm=llm,
    )
    md = render_temporal_md(res)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    sys.stdout.write(md)
    return gate_exit_code(res)


if __name__ == "__main__":
    raise SystemExit(main())
