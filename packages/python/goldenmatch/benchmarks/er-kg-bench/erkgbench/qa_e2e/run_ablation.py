"""CLI: run the ER-quality ablation, write ABLATION.md, exit non-zero on a HARD
assertion failure (monotonic decay + hop-widening). The soft assertion
(goldengraph>=name_only) prints WARN, never gates.

Needs the goldengraph_native wheel. Example:
    python -m erkgbench.qa_e2e.run_ablation --seed 7 --n-questions 80 \
        --ambiguity 0.6 --out-md ABLATION.md
"""
from __future__ import annotations

import argparse
import sys

from .ablation import gate_exit_code, render_ablation_md, run_ablation


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--n-questions", type=int, default=80)
    p.add_argument("--ambiguity", type=float, default=0.6)
    p.add_argument("--max-hops", type=int, default=4)
    p.add_argument("--out-md", default="ABLATION.md")
    args = p.parse_args(argv)

    res = run_ablation(
        seed=args.seed,
        n_questions=args.n_questions,
        ambiguity=args.ambiguity,
        max_hops=args.max_hops,
    )
    md = render_ablation_md(res)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    sys.stdout.write(md)
    return gate_exit_code(res)


if __name__ == "__main__":
    raise SystemExit(main())
