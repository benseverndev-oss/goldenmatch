"""Cross-surface lock for the regression decision logic (baseline strategy +
direction-aware policy). The TS side (`regressions.parity.test.ts`) recomputes the
same cases against the SAME data-driven fixture.

`regressions_cases.json` is a byte-identical copy shared with
`packages/typescript/goldenanalysis/tests/fixtures/`. Inputs are JSON-safe (finite
floats + strings), so the fixture is fully data-driven: each case carries its `input`
and the Python-locked `expected` {baseline, delta_pct, flagged}. This enforces the
Python<->TS parity that a Rust cutover would guarantee by construction — without the
plumbing, for trivial rule logic with no muscle (see
`docs/superpowers/specs/2026-07-06-goldenanalysis-quality-regressions-parity.md`).

Adversarial coverage: even/odd median, baseline==0, negative baseline, threshold
boundary (inclusive), all three directions, window>history, and empty history.
"""

import json
from pathlib import Path

from goldenanalysis import _regressions as reg

FIXTURE = Path(__file__).parent / "fixtures" / "regressions_cases.json"


def test_regression_cases_match_fixture() -> None:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for name, case in cases.items():
        inp = case["input"]
        base = reg.baseline_value(inp["history"], inp["strategy"], window=inp["window"])
        got = {"baseline": base}
        if base is not None:
            got["delta_pct"] = reg.delta_pct(base, inp["current"])
            got["flagged"] = reg.is_regression(
                inp["direction"], base, inp["current"], inp["threshold_pct"]
            )
        assert got == case["expected"], name
