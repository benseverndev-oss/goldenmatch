#!/usr/bin/env python3
"""Emit the Python-oracle half of the standardizer cross-surface characterization.

The goldenmatch `standardize` phase is DIVERGENT across Python and TypeScript
(decision 0046 verdict table lists "standardize/transforms" as not byte-portable).
The original ADR named dates as the example, but goldenmatch ships no date
standardizer -- the real, measurable divergence is in the standardizer SET
(null-vs-empty for invalid input, whitespace handling, title-casing). This script
+ the paired TS test (standardizer-conformance.parity.test.ts) turn that prose
claim into a runnable CHARACTERIZATION that pins the divergence so it cannot
silently widen (thesis-conformance decision 0047, weakness
`standardize-dates-no-conformance-test`).

This regenerates ONLY the `python` block of the fixture; the committed
`typescript` block is preserved (regenerate it via the probe documented in the TS
test). Run: `python3 scripts/emit_standardizer_conformance_fixture.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

from goldenmatch.core.standardize import get_standardizer

FIXTURE = (Path(__file__).resolve().parent.parent
           / "packages/typescript/goldenmatch/tests/parity/fixtures"
           / "standardizer-conformance.json")


def build_python(standardizers: list[str], inputs: list[str]) -> dict:
    out: dict[str, dict[str, str | None]] = {}
    for name in standardizers:
        fn = get_standardizer(name)
        out[name] = {s: fn(s) for s in inputs}
    return out


def main() -> int:
    fixture = json.loads(FIXTURE.read_text())
    standardizers = fixture["standardizers"]
    inputs = fixture["inputs"]
    py = build_python(standardizers, inputs)
    ts = fixture["typescript"]

    agree = diverge = 0
    per: dict[str, int] = {}
    for n in standardizers:
        per[n] = sum(1 for s in inputs if py[n][s] != ts[n].get(s))
        for s in inputs:
            if py[n][s] == ts[n].get(s):
                agree += 1
            else:
                diverge += 1

    fixture["python"] = py
    fixture["summary"] = {
        "total": agree + diverge, "agree": agree, "diverge": diverge,
        "per_standardizer_diverge": {k: v for k, v in per.items()},
    }
    FIXTURE.write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {FIXTURE} (agree={agree} diverge={diverge})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
