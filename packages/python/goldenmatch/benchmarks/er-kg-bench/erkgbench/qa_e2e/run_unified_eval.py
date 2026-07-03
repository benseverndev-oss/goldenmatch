"""CLI: slice 4a unified planner gate; write UNIFIED.md, exit non-zero on a HARD gate failure.
Key-free; the justification needs the goldengraph_native wheel.
"""
from __future__ import annotations

import argparse
import sys

from .unified_eval import gate_exit_code, render_unified_md, run_unified_deterministic


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GoldenGraph unified planner gate")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--n-anchors", type=int, default=60)
    ap.add_argument("--n-facts", type=int, default=40)
    ap.add_argument("--n-questions", type=int, default=80)
    ap.add_argument("--out-md", default="UNIFIED.md")
    args = ap.parse_args(argv)
    res = run_unified_deterministic(seed=args.seed, n_anchors=args.n_anchors,
                                    n_facts=args.n_facts, n_questions=args.n_questions)
    md = render_unified_md(res)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    sys.stdout.write(md)
    return gate_exit_code(res)


if __name__ == "__main__":
    raise SystemExit(main())
