"""Certified key discovery (semantic-model discovery, Phase 1).

`discover_keys` proposes single-column entity keys via cheap signals
(identifier / cardinality / fd) and PROVES each with `certify_key_integrity`, so a
`KeyCandidate` is pre-graded. Design:
`docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`.
"""
from __future__ import annotations

import pyarrow as pa
from goldenmatch.semantic import KeyCandidate, discover_keys


def _orders() -> pa.Table:
    # order_id is the clean key; customer_id fans out (c1 twice); status is a
    # low-cardinality dimension; amount is a numeric measure.
    return pa.table(
        {
            "order_id": ["o1", "o2", "o3", "o4"],
            "customer_id": ["c1", "c1", "c2", "c3"],
            "status": ["new", "new", "done", "new"],
            "amount": [10.0, 20.0, 30.0, 40.0],
        }
    )


def test_clean_key_is_proposed_and_certified_trustworthy() -> None:
    cands = discover_keys(_orders())
    assert cands, "should propose at least one candidate"
    top = cands[0]
    assert isinstance(top, KeyCandidate)
    # The clean primary key ranks first and is certified trustworthy.
    assert top.columns == ["order_id"]
    assert top.is_trustworthy is True
    assert top.certificate.max_fan_out == 1.0
    assert "identifier" in top.signals and "cardinality" in top.signals


def test_fanned_out_key_is_flagged_untrustworthy() -> None:
    cands = {c.columns[0]: c for c in discover_keys(_orders())}
    # customer_id is proposed (it's identifier-shaped) but certified UNTRUSTWORTHY:
    # a metric grouped on it would double-count.
    assert "customer_id" in cands
    cust = cands["customer_id"]
    assert cust.is_trustworthy is False
    assert cust.certificate.max_fan_out == 2.0
    # It ranks below the clean key.
    order = [c.columns[0] for c in discover_keys(_orders())]
    assert order.index("order_id") < order.index("customer_id")


def test_numeric_and_dimension_columns_are_not_proposed_as_keys() -> None:
    proposed = {c.columns[0] for c in discover_keys(_orders())}
    # `amount` is numeric (a measure) — excluded from the cardinality signal even
    # though it's unique in this tiny sample. `status` is low-cardinality (a dim).
    assert "amount" not in proposed
    assert "status" not in proposed


def test_no_clean_key_returns_only_untrustworthy_candidates() -> None:
    # A table whose every id-shaped column duplicates → the loud "this grain will
    # double-count" signal: proposed candidates exist, none trustworthy.
    t = pa.table(
        {
            "order_id": ["o1", "o1", "o2"],   # duplicated
            "customer_id": ["c1", "c2", "c2"],  # duplicated
        }
    )
    cands = discover_keys(t)
    assert cands  # candidates are still proposed (id-shaped)
    assert all(not c.is_trustworthy for c in cands)


def test_signals_and_score_are_populated() -> None:
    top = discover_keys(_orders())[0]
    assert top.signals == sorted(top.signals)  # deterministic order
    assert 0.0 <= top.score <= 1.0
    # a certified-clean key scores at the top of the range.
    assert top.score >= top.certificate.estimate


def test_max_candidates_caps_output() -> None:
    t = pa.table({f"id{i}": [f"v{i}{j}" for j in range(4)] for i in range(6)})
    assert len(discover_keys(t, max_candidates=3)) <= 3
