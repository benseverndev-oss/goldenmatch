"""Tests for the goldenanalysis ``key.integrity`` analyzer."""
from __future__ import annotations

from goldenanalysis.models import AnalyzerInput
from goldenanalysis.registry import available_analyzers, load_analyzer


def test_registered():
    assert "key.integrity" in available_analyzers()


def _cert(**overrides):
    base = dict(
        key_columns=["customer_id"],
        grain=None,
        n_rows=3,
        n_key_groups=2,
        is_unique_at_grain=False,
        duplicate_key_groups=1,
        max_fan_out=2.0,
        measure_fan_out={"revenue": 1.6667},
        resolved_entities=None,
        fragmented_entities=None,
        undercount_estimate=None,
        estimable=True,
        note="",
    )
    base.update(overrides)
    return base


def test_projects_structural_metrics_from_dict():
    a = load_analyzer("key.integrity")
    res = a.run(AnalyzerInput(dataset="t", artifacts={"key_certificate": _cert()}))
    m = {metric.key: metric.value for metric in res.metrics}
    assert m["key.uniqueness"] == 0.5
    assert m["key.duplicate_groups"] == 1
    assert m["key.max_fan_out"] == 2.0
    # undercount/fragmented omitted when resolution wasn't run
    assert "key.undercount_estimate" not in m
    fan = {t.name: t.rows for t in res.tables}
    assert fan["measure_fan_out"] == [["revenue", 1.6667]]


def test_emits_undercount_when_resolved():
    a = load_analyzer("key.integrity")
    cert = _cert(resolved_entities=1, fragmented_entities=1, undercount_estimate=1.0)
    res = a.run(AnalyzerInput(dataset="t", artifacts={"key_certificate": cert}))
    m = {metric.key: metric.value for metric in res.metrics}
    assert m["key.undercount_estimate"] == 1.0
    assert m["key.fragmented_entities"] == 1


def test_no_certificate_is_empty():
    a = load_analyzer("key.integrity")
    res = a.run(AnalyzerInput(dataset="t", artifacts={}))
    assert res.metrics == []
    assert res.tables == []


def test_consumes_real_certificate_object():
    from goldenmatch.core.key_integrity_certificate import KeyIntegrityCertificate

    cert = KeyIntegrityCertificate(
        key_columns=["id"], grain=None, n_rows=4, n_key_groups=3,
        is_unique_at_grain=False, duplicate_key_groups=1, max_fan_out=2.0,
        measure_fan_out={"amt": 1.5},
    )
    a = load_analyzer("key.integrity")
    res = a.run(AnalyzerInput(dataset="t", artifacts={"key_certificate": cert}))
    m = {metric.key: metric.value for metric in res.metrics}
    assert m["key.uniqueness"] == cert.estimate
