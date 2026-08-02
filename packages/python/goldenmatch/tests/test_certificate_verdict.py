"""Certificate trust-verdict block (`certificate_verdict`) + its cross-surface
write-back into the Cube / OSI / MetricFlow catalog emitters.

`certificate_verdict` is a pure projection of a `KeyIntegrityCertificate` into the
`key_integrity` metadata a semantic catalog carries — the single source the three
dialect emitters embed. Single-sourced with the TS port (`certificateVerdict`) via
a shared fixture (read directly from the TS parity tree — no copy).
"""
from __future__ import annotations

import json
from pathlib import Path

from goldenmatch.core.key_integrity_certificate import (
    KeyIntegrityCertificate,
    certificate_verdict,
)
from goldenmatch.semantic.key_integrity import _wilson_interval

FIXTURE = (
    Path(__file__).parent.parent
    / ".."
    / ".."
    / "typescript"
    / "goldenmatch"
    / "tests"
    / "parity"
    / "fixtures"
    / "key-integrity"
    / "certificate_verdict_cases.json"
)


def _cert_from_case(case: dict) -> KeyIntegrityCertificate:
    init = case["init"]
    cert = KeyIntegrityCertificate(
        key_columns=init["keyColumns"],
        grain=init["grain"],
        n_rows=init["nRows"],
        n_key_groups=init["nKeyGroups"],
        is_unique_at_grain=init["isUniqueAtGrain"],
        duplicate_key_groups=init["duplicateKeyGroups"],
        max_fan_out=init["maxFanOut"],
        measure_fan_out=dict(init["measureFanOut"]),
    )
    res = case["resolution"]
    if res is not None:
        cert.resolved_entities = res["resolved_entities"]
        cert.fragmented_entities = res["fragmented_entities"]
        cert.undercount_estimate = res["undercount_estimate"]
        cert.undercount_ci_low = res["undercount_ci_low"]
        cert.undercount_ci_high = res["undercount_ci_high"]
    return cert


def test_matches_fixture() -> None:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    assert cases
    for case in cases:
        cert = _cert_from_case(case)
        assert certificate_verdict(cert) == case["expected"], case["name"]


def test_structural_only_omits_resolution_keys() -> None:
    cert = KeyIntegrityCertificate(
        key_columns=["k"], grain=None, n_rows=5, n_key_groups=4,
        is_unique_at_grain=False, duplicate_key_groups=1, max_fan_out=2.0,
        measure_fan_out={"revenue": 1.4},
    )
    block = certificate_verdict(cert)
    assert block["verdict"] == "untrustworthy"
    assert block["unique_at_grain"] is False
    assert block["uniqueness_estimate"] == 0.75
    assert block["measure_fan_out"] == {"revenue": 1.4}
    # No resolution run → no undercount interval, safe_bound collapses to estimate.
    assert "undercount_ci" not in block
    assert block["undercount_estimate"] is None
    assert block["safe_bound"] == 0.75
    assert block["safe_bound_conservative"] == 0.75


def test_measure_fan_out_omitted_when_empty() -> None:
    cert = KeyIntegrityCertificate(
        key_columns=["k"], grain=None, n_rows=3, n_key_groups=3,
        is_unique_at_grain=True, duplicate_key_groups=0, max_fan_out=1.0,
    )
    block = certificate_verdict(cert)
    assert "measure_fan_out" not in block
    assert block["verdict"] == "trustworthy"


def test_conservative_bound_discounts_ci_upper() -> None:
    cert = KeyIntegrityCertificate(
        key_columns=["k"], grain=None, n_rows=5, n_key_groups=4,
        is_unique_at_grain=False, duplicate_key_groups=1, max_fan_out=2.0,
        measure_fan_out={"revenue": 1.4},
    )
    cert.resolved_entities = 2
    cert.fragmented_entities = 1
    cert.undercount_estimate = 0.5
    lo, hi = _wilson_interval(1, 2)
    cert.undercount_ci_low, cert.undercount_ci_high = lo, hi
    block = certificate_verdict(cert)
    assert block["undercount_ci"] == [lo, hi]
    assert block["safe_bound"] == 0.5
    assert block["safe_bound_conservative"] == min(0.75, 1.0 - hi)


def test_emitters_write_back_the_verdict() -> None:
    # All three dialect emitters embed the same verdict block for a resolved cert.
    import pyarrow as pa
    from goldenmatch.semantic.crosswalk import ResolvedCrosswalk
    from goldenmatch.semantic.cube import emit_cube_from_crosswalk
    from goldenmatch.semantic.metricflow import emit_from_crosswalk
    from goldenmatch.semantic.osi import emit_osi_from_crosswalk

    cert = KeyIntegrityCertificate(
        key_columns=["customer_id"], grain=None, n_rows=5, n_key_groups=4,
        is_unique_at_grain=False, duplicate_key_groups=1, max_fan_out=2.0,
        measure_fan_out={"revenue": 1.4},
    )
    cert.resolved_entities = 2
    cert.fragmented_entities = 1
    cert.undercount_estimate = 0.5
    cert.undercount_ci_low, cert.undercount_ci_high = _wilson_interval(1, 2)

    xw = ResolvedCrosswalk(
        table=pa.table({"source": [], "source_pk": [], "resolved_entity_id": []}),
        source="orders", source_pk_column="customer_id",
        resolved_key="resolved_entity_id", n_records=5, n_entities=4,
    )
    for out in (
        emit_cube_from_crosswalk(xw, source_cube="orders", certificate=cert),
        emit_osi_from_crosswalk(xw, source_dataset="orders", certificate=cert),
        emit_from_crosswalk(xw, "orders", measures=["revenue"], certificate=cert),
    ):
        assert "verdict: untrustworthy" in out
        assert "safe_bound_conservative:" in out

    # No certificate → no key_integrity block (unchanged default).
    assert "key_integrity" not in emit_cube_from_crosswalk(xw, source_cube="orders")
    assert "key_integrity" not in emit_from_crosswalk(xw, "orders")
