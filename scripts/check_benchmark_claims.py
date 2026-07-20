#!/usr/bin/env python3
"""Gate: headline benchmark/perf numbers stay consistent across the AI-facing
surfaces and match a single declared source of truth.

The F1 scores, "beats Splink" deltas, DQbench composite, PPRL F1, and the 100M
scale timing are the highest-stakes claims in the llms.txt + READMEs, and they
were tied to nothing -- a re-benchmarked or regressed number could update one
surface (or none) and leave the others boasting a stale figure. This anchors them
to docs/benchmark-claims.json: for every claim, any tracked surface that mentions
the metric's context token must also carry the declared value.

LIMITATION: this enforces consistency with the declared value, not agreement with
a fresh benchmark run. Changing a value is a deliberate edit that should follow a
real run (authority: docs/benchmarks/). Stdlib-only, so it runs in the fast
docs_consistency job with no package import.

Run: python scripts/check_benchmark_claims.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAIMS = ROOT / "docs" / "benchmark-claims.json"


def main() -> int:
    spec = json.loads(CLAIMS.read_text(encoding="utf-8"))
    surfaces = spec["surfaces"]
    claims = spec["claims"]

    texts: dict[str, str] = {}
    for rel in surfaces:
        path = ROOT / rel
        if not path.exists():
            print(f"benchmark-claims gate FAILED: tracked surface missing: {rel}")
            return 1
        texts[rel] = path.read_text(encoding="utf-8")

    errors: list[str] = []
    verified = 0
    for claim in claims:
        ctx, val, cid = claim["context"], claim["value"], claim["id"]
        cited_anywhere = False
        for rel, text in texts.items():
            if ctx in text:
                cited_anywhere = True
                if val not in text:
                    errors.append(f"{rel}: cites '{ctx}' ({cid}) but not its declared "
                                  f"value '{val}'")
                else:
                    verified += 1
        if not cited_anywhere:
            errors.append(f"no tracked surface cites '{ctx}' ({cid}) -- stale claim in "
                          "benchmark-claims.json, or the surfaces dropped it")

    if errors:
        print("benchmark-claims gate FAILED:")
        for e in errors:
            print(f"  - {e}")
        print("\nReconcile docs/benchmark-claims.json with the surfaces (update both "
              "after a fresh run; authority: docs/benchmarks/).")
        return 1
    print(f"benchmark-claims gate OK: {verified} surface citations match the "
          f"{len(claims)} declared headline numbers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
