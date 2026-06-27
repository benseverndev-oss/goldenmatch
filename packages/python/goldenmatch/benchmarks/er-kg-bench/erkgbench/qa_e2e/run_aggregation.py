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
import sys

from .aggregation import gate_exit_code, render_aggregation_md, run_aggregation_deterministic


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="GoldenGraph aggregation capability bench")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--n-anchors", type=int, default=60)
    p.add_argument("--ambiguity", type=float, default=0.6)
    p.add_argument("--passage-k", type=int, default=10)
    p.add_argument("--out-md", default="AGGREGATION.md")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    res = run_aggregation_deterministic(
        seed=args.seed, n_anchors=args.n_anchors,
        ambiguity=args.ambiguity, passage_k=args.passage_k,
    )
    md = render_aggregation_md(res)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    sys.stdout.write(md)
    return gate_exit_code(res)


if __name__ == "__main__":
    raise SystemExit(main())
