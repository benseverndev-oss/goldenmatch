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
