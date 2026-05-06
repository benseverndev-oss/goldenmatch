"""Pair records returned by the run/evaluation routers carry NL prose.

`web/pair_prose.py` enriches pair dicts with a one-line template
explanation derived from the field-level breakdown. Verify the
enrichment is applied at the surface points the UI consumes.
"""
from __future__ import annotations


def test_cluster_detail_pairs_carry_prose(client):
    body = client.get("/api/v1/runs/20260101_000000/clusters/1").json()
    assert body["pairs"], "fixture cluster 1 has one pair"
    p = body["pairs"][0]
    assert "prose" in p
    assert isinstance(p["prose"], str) and p["prose"]
    # Mentions the field name and score; explain_pair_nl is template-driven
    # so this is a stable substring check, not a phrasing check.
    assert "name" in p["prose"]


def test_run_review_pairs_carry_prose(client):
    rows = client.get("/api/v1/runs/20260101_000000/review?lo=0.0").json()
    assert rows
    assert "prose" in rows[0]


def test_evaluation_tp_pairs_carry_prose(client):
    client.post(
        "/api/v1/labels",
        json={"row_id_a": 0, "row_id_b": 1, "label": "match"},
    )
    body = client.get("/api/v1/runs/20260101_000000/evaluation").json()
    assert body["tp"]
    assert "prose" in body["tp"][0]


def test_evaluation_fn_stub_does_not_carry_prose(client):
    """FN pairs without a lineage record have nothing to explain — they're
    rendered as a stub with no `fields`. Skipping prose keeps the contract
    that `prose` reflects the actual breakdown rather than fabricating one."""
    client.post(
        "/api/v1/labels",
        json={"row_id_a": 0, "row_id_b": 2, "label": "match"},
    )
    body = client.get("/api/v1/runs/20260101_000000/evaluation").json()
    assert body["fn"]
    assert "prose" not in body["fn"][0]
