"""Per-run labels endpoint: GET /api/v1/runs/{name}/labels.

Labels are dataset-level (canonical pair as key, no run_name). This route
intersects the global labels store with a specific run's lineage so the
inspector can answer "what have I labeled in THIS run".
"""
from __future__ import annotations


def test_run_labels_filters_to_lineage_pairs(client):
    # Pair (0, 1) appears in cluster 1 of the fixture run.
    client.post(
        "/api/v1/labels",
        json={"row_id_a": 0, "row_id_b": 1, "label": "match"},
    )
    # A pair that does NOT appear in this run's lineage should be filtered out.
    client.post(
        "/api/v1/labels",
        json={"row_id_a": 0, "row_id_b": 2, "label": "non_match"},
    )

    resp = client.get("/api/v1/runs/20260101_000000/labels")
    assert resp.status_code == 200
    out = resp.json()
    # Only the (0, 1) label survives — (0, 2) isn't in this run's pairs.
    assert len(out) == 1
    assert (out[0]["row_id_a"], out[0]["row_id_b"]) == (0, 1)
    assert out[0]["label"] == "match"
    # The endpoint stamps each label with its cluster_id for navigation.
    assert out[0]["cluster_id"] == 1


def test_run_labels_404_on_unknown_run(client):
    resp = client.get("/api/v1/runs/nope/labels")
    assert resp.status_code == 404
