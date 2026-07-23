"""CLI: run the deterministic aggregation capability bench (goldengraph exact
traversal vs a passage-window floor), write AGGREGATION.md, exit non-zero on a HARD
gate failure (size-invariant + collapse + widening gap). Key-free; needs the
goldengraph_native wheel.

Example:
    python -m erkgbench.qa_e2e.run_aggregation --seed 7 --n-anchors 60 \
        --ambiguity 0.6 --passage-k 10 --out-md AGGREGATION.md
"""
from __future__ import annotations

import argparse
import os
import sys

from .aggregation import gate_exit_code, render_aggregation_md, run_aggregation_deterministic


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="GoldenGraph aggregation capability bench")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--n-anchors", type=int, default=60)
    p.add_argument("--ambiguity", type=float, default=0.6)
    p.add_argument("--passage-k", type=int, default=10)
    p.add_argument("--out-md", default="AGGREGATION.md")
    p.add_argument("--with-llm", action="store_true",
                   help="also score the realistic real-LLM RAG floor (needs OPENAI_API_KEY)")
    p.add_argument("--budget-usd", type=float, default=2.0)
    p.add_argument("--source", choices=("synthetic", "realworld"), default="synthetic",
                   help="synthetic fan-out corpus (default) or the committed Wikidata fixture")
    p.add_argument("--fixture", default=None,
                   help="realworld source only: path to the committed Wikidata fixture JSON")
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
    if args.source == "realworld":
        from .realworld import _FIXTURE_DIR, run_realworld_aggregation

        fixture = args.fixture or (_FIXTURE_DIR / "wikidata_companies_v1.json")
        res = run_realworld_aggregation(
            fixture, ambiguity=args.ambiguity, passage_k=args.passage_k, llm=llm,
        )
    else:
        res = run_aggregation_deterministic(
            seed=args.seed, n_anchors=args.n_anchors,
            ambiguity=args.ambiguity, passage_k=args.passage_k, llm=llm,
        )
    md = render_aggregation_md(res)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    sys.stdout.write(md)
    return gate_exit_code(res)


if __name__ == "__main__":
    raise SystemExit(main())
