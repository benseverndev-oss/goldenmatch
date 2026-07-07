"""CLI: run the ER->answer ablation across the ambiguity SWEEP, write
RESULTS_ER_ANSWER_ABLATION.md. Opt-in + budget-capped (one cap spans the whole sweep).
Needs OPENAI_API_KEY + the goldengraph_native wheel.

Answers the World-A/B question behind the RESULTS_QA_E2E.md anomaly: does the ER->answer
advantage SURVIVE rising ambiguity (World A) or collapse (World B)? Exits non-zero ONLY on
the HARD monotonicity assertion; the delta-holds verdict is SOFT (it's the finding).

Example:
    python -m erkgbench.qa_e2e.run_answer_ablation_sweep --seed 7 --n-questions 80 \
        --ambiguity-sweep 0,0.25,0.5,0.75,1.0 --model gpt-4o-mini --max-cost-usd 5 \
        --out-md results/RESULTS_ER_ANSWER_ABLATION.md
"""
from __future__ import annotations

import argparse
import os
import sys


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="GoldenGraph ER->answer ablation sweep")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--n-questions", type=int, default=80)
    p.add_argument("--ambiguity-sweep", default="0,0.25,0.5,0.75,1.0",
                   help="comma-separated ambiguity grid")
    p.add_argument("--max-hops", type=int, default=4)
    p.add_argument("--model", default="gpt-4o-mini")
    p.add_argument("--max-cost-usd", type=float, default=5.0)
    p.add_argument("--out-md", default="results/RESULTS_ER_ANSWER_ABLATION.md")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    ambiguities = tuple(float(x) for x in args.ambiguity_sweep.split(","))

    from .answer_ablation_sweep import (
        render_sweep_md,
        run_answer_ablation_sweep,
        sweep_verdict,
    )

    if not os.environ.get("OPENAI_API_KEY"):
        sys.stderr.write("No OPENAI_API_KEY; skipping the ER->answer sweep (opt-in lane).\n")
        return 0

    from goldengraph.llm import OpenAIClient

    from .scorecard_llm import BudgetConfig, BudgetTracker, _BudgetedLLM

    # ONE budget across the whole sweep: the llm.exhausted short-circuit inside
    # answer_match_ablation zeros further synthesis once the cap is hit.
    tracker = BudgetTracker(BudgetConfig(max_cost_usd=args.max_cost_usd))
    llm = _BudgetedLLM(OpenAIClient(model=args.model), tracker, model=args.model)

    sweep = run_answer_ablation_sweep(
        seed=args.seed,
        n_questions=args.n_questions,
        ambiguities=ambiguities,
        max_hops=args.max_hops,
        llm=llm,
    )
    md = render_sweep_md(sweep, model=args.model)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    sys.stdout.write(md)

    # Gate on HARD assertions only (monotonicity). The delta-holds verdict is SOFT — a WARN
    # there is the World-B finding, not a failed run.
    hard_failed = any(is_hard and not passed for _l, passed, is_hard in sweep_verdict(sweep))
    return 1 if hard_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
