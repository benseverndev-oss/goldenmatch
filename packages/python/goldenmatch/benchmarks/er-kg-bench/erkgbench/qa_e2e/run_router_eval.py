"""CLI: deterministic query-router gate (classifier accuracy + routed-aggregate correctness at
ambiguity=0.0); write ROUTER.md, exit non-zero on a HARD gate failure. Key-free; needs the
goldengraph_native wheel. --with-llm adds the opt-in auto-vs-local answer-match row.

Example:
    python -m erkgbench.qa_e2e.run_router_eval --seed 7 --n-anchors 60 --out-md ROUTER.md
"""
from __future__ import annotations

import argparse
import os
import sys

from .router_eval import gate_exit_code, render_router_md, run_router_deterministic


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GoldenGraph query-router gate")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--n-anchors", type=int, default=60)
    ap.add_argument("--out-md", default="ROUTER.md")
    ap.add_argument("--with-llm", action="store_true")
    ap.add_argument("--budget-usd", type=float, default=3.0)
    ap.add_argument("--llm-out-md", default="ROUTER_LLM.md")
    args = ap.parse_args(argv)

    res = run_router_deterministic(seed=args.seed, n_anchors=args.n_anchors)
    md = render_router_md(res)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    sys.stdout.write(md)

    if args.with_llm and os.environ.get("OPENAI_API_KEY"):
        from goldenmatch.config.schemas import BudgetConfig
        from goldenmatch.core.llm_budget import BudgetTracker

        from .router_eval import render_router_llm_md, run_router_llm

        tr = BudgetTracker(BudgetConfig(max_cost_usd=args.budget_usd))
        lm = render_router_llm_md(run_router_llm(seed=args.seed, n_anchors=args.n_anchors, tracker=tr))
        with open(args.llm_out_md, "w", encoding="utf-8") as fh:
            fh.write(lm)
        sys.stdout.write(lm)

    return gate_exit_code(res)


if __name__ == "__main__":
    raise SystemExit(main())
