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


def test_with_prose_copies_pair_and_skips_recompute_when_already_present(monkeypatch):
    """`with_prose`'s docstring: a shallow copy of its OWN `pair` argument with
    a `prose` key added, skipping recomputation when `prose` is already
    present and non-empty. Unit-level (no `client` fixture needed) -- this is
    about the function's own contract, not a router surface."""
    from goldenmatch.web import pair_prose

    calls = []
    real_explain = pair_prose.explain_pair_nl

    def _spy(**kwargs):
        calls.append(kwargs)
        return real_explain(**kwargs)

    monkeypatch.setattr(pair_prose, "explain_pair_nl", _spy)

    pair = {"a": 1, "b": 2}
    enriched = pair_prose.with_prose(pair)

    # (a) original keys/values present unchanged
    assert enriched["a"] == 1
    assert enriched["b"] == 2
    # original dict itself is untouched -- a COPY, not a mutation
    assert pair == {"a": 1, "b": 2}

    # (b) a new prose key was added
    assert "prose" in enriched
    assert isinstance(enriched["prose"], str) and enriched["prose"]
    assert len(calls) == 1

    # (c) calling again with prose already present does NOT recompute
    again = pair_prose.with_prose(enriched)
    assert again["prose"] == enriched["prose"]
    assert len(calls) == 1, "prose already present and non-empty -- must not recompute"


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
