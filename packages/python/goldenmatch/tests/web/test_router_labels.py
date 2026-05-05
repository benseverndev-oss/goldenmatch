from __future__ import annotations


def test_post_label_appends(client, sample_project):
    body = {"row_id_a": 0, "row_id_b": 1, "label": "match", "note": "obvious"}
    assert client.post("/api/v1/labels", json=body).status_code == 200
    listed = client.get("/api/v1/labels").json()
    assert len(listed) == 1
    assert listed[0]["label"] == "match"


def test_relabel_overrides(client):
    a = {"row_id_a": 0, "row_id_b": 1, "label": "match"}
    b = {"row_id_a": 0, "row_id_b": 1, "label": "non_match"}
    client.post("/api/v1/labels", json=a)
    client.post("/api/v1/labels", json=b)
    listed = client.get("/api/v1/labels").json()
    assert len(listed) == 1
    assert listed[0]["label"] == "non_match"


def test_invalid_label_rejected(client):
    bad = {"row_id_a": 0, "row_id_b": 1, "label": "maybe"}
    assert client.post("/api/v1/labels", json=bad).status_code == 422


def test_self_pair_rejected(client):
    """row_id_a == row_id_b is meaningless and would pollute the dedup table."""
    bad = {"row_id_a": 5, "row_id_b": 5, "label": "match"}
    assert client.post("/api/v1/labels", json=bad).status_code == 422


def test_pair_canonicalization_dedups_swapped_orderings(client):
    """Labeling (0,1) then relabeling (1,0) must hit the same canonical key.

    The rest of the codebase canonicalizes pair keys as (min, max); the
    inspector may surface a pair in either order, so the labels store has to
    match that invariant or we'd get phantom duplicates.
    """
    client.post("/api/v1/labels", json={"row_id_a": 0, "row_id_b": 1, "label": "match"})
    client.post("/api/v1/labels", json={"row_id_a": 1, "row_id_b": 0, "label": "non_match"})
    listed = client.get("/api/v1/labels").json()
    assert len(listed) == 1
    assert listed[0]["label"] == "non_match"
    # canonical (min, max) on read
    assert (listed[0]["row_id_a"], listed[0]["row_id_b"]) == (0, 1)


def test_note_and_timestamp_round_trip(client):
    body = {"row_id_a": 2, "row_id_b": 3, "label": "match", "note": "obvious dup"}
    posted = client.post("/api/v1/labels", json=body).json()
    assert posted["note"] == "obvious dup"
    assert "ts" in posted and posted["ts"].endswith("+00:00")  # UTC isoformat
    listed = client.get("/api/v1/labels").json()
    rec = next(r for r in listed if (r["row_id_a"], r["row_id_b"]) == (2, 3))
    assert rec["note"] == "obvious dup"
