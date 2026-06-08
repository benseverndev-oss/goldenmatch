"""Canonical ``frame.summary`` report fixture — the P/TS parity anchor.

``analyze`` over the committed fixture frame must produce exactly this report
(modulo the volatile ``generated_at`` / ``run_id``). The future TypeScript port
(Phase 3) asserts against the same JSON file, so this locks the cross-surface
contract. Changing ``frame.summary`` output means regenerating this fixture on
purpose (and, once it exists, the TS one too).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import goldenanalysis as ga
from fixtures import build_customers_small

FIXTURE = Path(__file__).parent / "fixtures" / "report_frame_summary.json"

_VOLATILE = ("generated_at", "run_id")


def _strip_volatile(report: ga.AnalysisReport) -> dict[str, Any]:
    payload = report.model_dump(mode="json")
    for key in _VOLATILE:
        payload.pop(key, None)
    return payload


def _canonical_report() -> ga.AnalysisReport:
    return ga.analyze(build_customers_small(), analyzers=["frame.summary"], dataset="customers")


def test_canonical_report_matches_fixture() -> None:
    got = _strip_volatile(_canonical_report())
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert got == expected


def test_schema_version_is_one() -> None:
    assert _canonical_report().schema_version == 1
