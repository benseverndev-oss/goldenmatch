"""Per-run review queue: GET /api/v1/runs/{name}/review.

Surfaces candidate pairs (pairs in the score band [lo, hi]) that the
steward hasn't labeled yet — one-at-a-time triage worklist.
"""
from __future__ import annotations


def test_review_returns_unlabeled_pairs(client):
    # Default band [0.5, 1.0] — the fixture's single pair scored 0.9.
    resp = client.get("/api/v1/runs/20260101_000000/review")
    assert resp.status_code == 200
    out = resp.json()
    assert len(out) == 1
    pair = out[0]
    assert (pair["row_id_a"], pair["row_id_b"]) == (0, 1)
    # Carries the full lineage pair record (fields + scores etc.).
    assert pair["fields"][0]["field"] == "name"


def test_review_excludes_already_labeled_pairs(client):
    # Label the (0, 1) pair, then ask for review queue — it should be empty.
    client.post(
        "/api/v1/labels",
        json={"row_id_a": 0, "row_id_b": 1, "label": "match"},
    )
    out = client.get("/api/v1/runs/20260101_000000/review").json()
    assert out == []


def test_review_include_labeled_returns_them(client):
    client.post(
        "/api/v1/labels",
        json={"row_id_a": 0, "row_id_b": 1, "label": "match"},
    )
    out = client.get(
        "/api/v1/runs/20260101_000000/review?include_labeled=true"
    ).json()
    assert len(out) == 1


def test_review_band_filters(client):
    # The only pair scores 0.9; band that excludes 0.9 → empty.
    out = client.get(
        "/api/v1/runs/20260101_000000/review?lo=0.95&hi=1.0"
    ).json()
    assert out == []
    out = client.get(
        "/api/v1/runs/20260101_000000/review?lo=0.85&hi=0.95"
    ).json()
    assert len(out) == 1


def test_review_404_on_unknown_run(client):
    assert client.get("/api/v1/runs/nope/review").status_code == 404
