"""quality.rollup analyzer (pure — dict findings + duck-typed manifest)."""

from __future__ import annotations

from types import SimpleNamespace

from goldenanalysis.analyzers.quality_rollup import QualityRollupAnalyzer
from goldenanalysis.models import AnalyzerInput

FINDINGS = [
    {"severity": "WARNING", "column": "email", "check": "email_blanked"},
    {"severity": "WARNING", "column": "email", "check": "email_blanked"},
    {"severity": "ERROR", "column": "phone", "check": "phone_unparseable"},
]
MANIFEST = SimpleNamespace(
    records=[
        SimpleNamespace(column="email", transform="blank_malformed", affected_rows=1188, total_rows=4000),
        SimpleNamespace(column="phone", transform="e164", affected_rows=12, total_rows=4000),
    ]
)


def _run(**artifacts):
    return QualityRollupAnalyzer().run(AnalyzerInput(dataset="customers", artifacts=artifacts))


def test_quality_and_flow_metrics() -> None:
    r = _run(findings=FINDINGS, manifest=MANIFEST)
    m = {x.key: x for x in r.metrics}
    assert m["quality.findings_total"].value == 3
    assert m["quality.findings_total"].direction == "lower_better"
    assert m["quality.columns_with_findings"].value == 2
    assert m["flow.rows_changed"].value == 1200
    assert m["flow.rules_fired"].value == 2
    assert "quality.score" not in m  # no profile supplied
    tbl = {t.name: t for t in r.tables}["findings_by_class"]
    rows = {row[0]: row[1] for row in tbl.rows}
    assert rows["email_blanked"] == 2 and rows["phone_unparseable"] == 1


def test_quality_score_from_profile() -> None:
    profile = SimpleNamespace(health_score=lambda findings_by_column: ("B", 80))
    r = _run(findings=FINDINGS, profile=profile)
    m = {x.key: x for x in r.metrics}
    assert m["quality.score"].value == 0.8
    assert m["quality.score"].direction == "higher_better"


def test_degrades_findings_only() -> None:
    m = {x.key: x for x in _run(findings=FINDINGS).metrics}
    assert "quality.findings_total" in m and "flow.rows_changed" not in m


def test_degrades_manifest_only() -> None:
    m = {x.key: x for x in _run(manifest=MANIFEST).metrics}
    assert "flow.rules_fired" in m and "quality.findings_total" not in m
