"""CLI: deterministic crossover recall bench (graph reachability vs lexical passage
floor) over ambiguity x passage_k; write CROSSOVER.md, exit non-zero on a HARD gate
failure. Key-free; needs the goldengraph_native wheel. --with-llm adds the opt-in
answer-match crossover table (needs OPENAI_API_KEY).

Example:
    python -m erkgbench.qa_e2e.run_crossover --seed 7 --n-questions 80 --out-md CROSSOVER.md
"""
from __future__ import annotations

import argparse
import os
import sys

from .crossover import gate_exit_code, recall_crossover_grid, render_crossover_md


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="GoldenGraph crossover bench")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--n-questions", type=int, default=80)
    p.add_argument("--max-hops", type=int, default=4)
    p.add_argument("--out-md", default="CROSSOVER.md")
    p.add_argument("--with-llm", action="store_true",
                   help="also score the opt-in real-LLM answer-match crossover (needs OPENAI_API_KEY)")
    p.add_argument("--budget-usd", type=float, default=3.0)
    p.add_argument("--llm-out-md", default="CROSSOVER_ANSWER_MATCH.md")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    res = recall_crossover_grid(seed=args.seed, n_questions=args.n_questions, max_hops=args.max_hops)
    md = render_crossover_md(res)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    sys.stdout.write(md)

    if args.with_llm and os.environ.get("OPENAI_API_KEY"):
        from goldengraph.llm import OpenAIClient
        from goldenmatch.config.schemas import BudgetConfig
        from goldenmatch.core.llm_budget import BudgetTracker

        from .crossover import answer_match_grid, render_answer_match_md
        from .scorecard_llm import _BudgetedLLM

        llm = _BudgetedLLM(
            OpenAIClient(model="gpt-4o-mini"),
            BudgetTracker(BudgetConfig(max_cost_usd=args.budget_usd)),
        )
        am = answer_match_grid(seed=args.seed, n_questions=args.n_questions,
                               max_hops=args.max_hops, llm=llm)
        am_md = render_answer_match_md(am)
        with open(args.llm_out_md, "w", encoding="utf-8") as fh:
            fh.write(am_md)
        sys.stdout.write(am_md)

    return gate_exit_code(res)  # gate is recall-only; answer-match is ungated


if __name__ == "__main__":
    raise SystemExit(main())
