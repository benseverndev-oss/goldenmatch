"""Cross-surface lock for the ``quality.rollup`` analyzer result. The TS side
(`qualityRollup.parity.test.ts`) runs its analyzer on the SAME artifacts and asserts
the byte-identical {metrics, tables} against the same fixture.

`quality_rollup_result.json` is a byte-identical copy shared with
`packages/typescript/goldenanalysis/tests/fixtures/`. Artifacts (findings/manifest) are
plain JSON-safe dicts, so each case carries its `input` and the Python-locked
`expected`. Enforces the Python<->TS parity of the drift-prone bits — the
``Counter.most_common`` tie ordering, unknown-check fallback, null-column filtering, and
the metric array order — without a Rust cutover (no clean object boundary; see
`docs/superpowers/specs/2026-07-06-goldenanalysis-quality-regressions-parity.md`).

Scope: the ``quality.score`` health-score path is NOT covered here (it calls back into
a GoldenCheck ``profile`` method, awkward to mock cross-surface); it stays in the
per-surface unit tests. All profile-free rollup logic is locked.
"""

import json
from pathlib import Path

from goldenanalysis.analyzers.quality_rollup import QualityRollupAnalyzer
from goldenanalysis.models import AnalyzerInput

FIXTURE = Path(__file__).parent / "fixtures" / "quality_rollup_result.json"


def test_quality_rollup_cases_match_fixture() -> None:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for name, case in cases.items():
        r = QualityRollupAnalyzer().run(AnalyzerInput(dataset="d", artifacts=case["input"]))
        got = {
            "metrics": [m.model_dump(mode="json") for m in r.metrics],
            "tables": [t.model_dump(mode="json") for t in r.tables],
        }
        assert got == case["expected"], name
