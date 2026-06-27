"""CLI: slice 4c unified entry-point gate; write UNIFIED_ENTRY.md, exit non-zero on a HARD failure.
Needs goldenmatch + the goldengraph_native wheel."""
from __future__ import annotations

import argparse
import sys

from .unified_entry_eval import (
    gate_exit_code,
    render_unified_entry_md,
    run_unified_entry_deterministic,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GoldenGraph unified entry-point gate")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--n-anchors", type=int, default=60)
    ap.add_argument("--out-md", default="UNIFIED_ENTRY.md")
    args = ap.parse_args(argv)
    res = run_unified_entry_deterministic(seed=args.seed, n_anchors=args.n_anchors)
    md = render_unified_entry_md(res)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    sys.stdout.write(md)
    return gate_exit_code(res)


if __name__ == "__main__":
    raise SystemExit(main())
