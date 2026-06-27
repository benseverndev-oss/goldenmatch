"""CLI: slice 4b tier-resolver gate; write TIER.md, exit non-zero on a HARD failure. Needs goldenmatch."""
from __future__ import annotations

import argparse
import sys

from .tier_eval import gate_exit_code, render_tier_md, run_tier_deterministic


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GoldenGraph tier-resolver gate")
    ap.add_argument("--out-md", default="TIER.md")
    args = ap.parse_args(argv)
    res = run_tier_deterministic()
    md = render_tier_md(res)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    sys.stdout.write(md)
    return gate_exit_code(res)


if __name__ == "__main__":
    raise SystemExit(main())
