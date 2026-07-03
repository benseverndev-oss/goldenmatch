"""CLI: deterministic KG-vs-KG capability scorecard (ER dial x capability); write KG_SCORECARD.md,
exit non-zero on a HARD gate failure. Key-free; needs the goldengraph_native wheel.
--with-frameworks adds the opt-in real-framework aggregation confirmation (needs OPENAI_API_KEY +
the engine extras/infra).

Example:
    python -m erkgbench.qa_e2e.run_kg_scorecard --seed 7 --n-questions 80 --n-anchors 60 \
        --ambiguity 0.6 --out-md KG_SCORECARD.md
"""
from __future__ import annotations

import argparse
import os
import sys

from .kg_scorecard import gate_exit_code, render_scorecard_md, run_scorecard_deterministic


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="GoldenGraph KG-vs-KG capability scorecard")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--n-questions", type=int, default=80)
    p.add_argument("--n-anchors", type=int, default=60)
    p.add_argument("--ambiguity", type=float, default=0.6)
    p.add_argument("--max-hops", type=int, default=4)
    p.add_argument("--out-md", default="KG_SCORECARD.md")
    p.add_argument("--with-frameworks", action="store_true",
                   help="also run the real-framework aggregation confirmation (needs OPENAI_API_KEY)")
    p.add_argument("--budget-usd", type=float, default=3.0)
    p.add_argument("--frameworks-out-md", default="KG_SCORECARD_FRAMEWORKS.md")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    res = run_scorecard_deterministic(
        seed=args.seed, n_questions=args.n_questions, n_anchors=args.n_anchors,
        ambiguity=args.ambiguity, max_hops=args.max_hops,
    )
    md = render_scorecard_md(res)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    sys.stdout.write(md)

    if args.with_frameworks and os.environ.get("OPENAI_API_KEY"):
        from goldenmatch.config.schemas import BudgetConfig
        from goldenmatch.core.llm_budget import BudgetTracker

        from .kg_scorecard import framework_aggregation_f1, render_framework_md

        # the inner LLM client + _BudgetedLLM wrapping happen INSIDE framework_aggregation_f1 per
        # engine; main() only owns the shared budget tracker.
        tracker = BudgetTracker(BudgetConfig(max_cost_usd=args.budget_usd))
        fr = framework_aggregation_f1(seed=args.seed, n_anchors=args.n_anchors,
                                      ambiguity=args.ambiguity, tracker=tracker)
        fr_md = render_framework_md(fr)
        with open(args.frameworks_out_md, "w", encoding="utf-8") as fh:
            fh.write(fr_md)
        sys.stdout.write(fr_md)

    return gate_exit_code(res)  # gate is the deterministic scorecard; frameworks lane is ungated


if __name__ == "__main__":
    raise SystemExit(main())
