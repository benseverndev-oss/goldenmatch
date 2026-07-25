#!/usr/bin/env python3
"""Phase 1 temporal end-to-end: 'who was CEO as of a past year?' on the committed Wikidata
CEO-history fixture. goldengraph store.as_of respects valid-time (right in BOTH regimes);
a temporal-blind RAG returns the most-recent CEO -> wrong on PAST queries. Writes
RESULTS_REALWORLD_TEMPORAL.md (as-of accuracy by regime, goldengraph vs the temporal-blind
floor). Needs the goldengraph-native wheel + goldenmatch. Key-free, offline.

Usage:
    python scripts/run_realworld_temporal_e2e.py \
        --fixture erkgbench/qa_e2e/fixtures/wikidata_ceo_temporal_v1.json \
        --out-md results/RESULTS_REALWORLD_TEMPORAL.md
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parents[1]
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from erkgbench.qa_e2e.realworld_temporal import _FIXTURE_DIR, run_realworld_temporal
from erkgbench.qa_e2e.temporal import render_temporal_md


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fixture", default=str(_FIXTURE_DIR / "wikidata_ceo_temporal_v1.json"))
    p.add_argument("--out-md", default="results/RESULTS_REALWORLD_TEMPORAL.md")
    args = p.parse_args(argv)

    t0 = time.perf_counter()
    res = run_realworld_temporal(Path(args.fixture))
    wall = time.perf_counter() - t0

    body = render_temporal_md(res)
    header = (
        "# Real-world temporal as-of -- goldengraph vs a temporal-blind RAG (Phase 1)\n\n"
        f"'Who was the CEO of X as of year D?' on the committed Wikidata CEO-history fixture "
        f"(`{Path(args.fixture).name}`, real successions with P580 start dates). PAST = a year "
        f"before the succession (gold = the earlier CEO); CURRENT = a recent year. Wall: "
        f"{wall:.1f}s.\n\n"
        "goldengraph's `store.as_of(D)` respects valid-time and is right in BOTH regimes; the "
        "temporal-blind floor returns the most-recent CEO -> correct on CURRENT, WRONG on PAST. "
        "This is a capability a text-RAG structurally lacks (no valid-time axis).\n\n"
    )
    out = Path(args.out_md)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(header + body, encoding="utf-8")
    print(header + body)
    print(f"[wrote {out}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
