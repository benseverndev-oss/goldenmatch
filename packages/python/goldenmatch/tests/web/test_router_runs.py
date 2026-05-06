from __future__ import annotations


def test_run_manifest(client):
    resp = client.get("/api/v1/runs/20260101_000000")
    assert resp.status_code == 200
    body = resp.json()
    assert body["row_count"] == 3
    assert body["cluster_count"] == 2
    assert body["total_pairs"] == 1


def test_run_manifest_404(client):
    assert client.get("/api/v1/runs/nope").status_code == 404


def test_clusters_paginated(client):
    resp = client.get("/api/v1/runs/20260101_000000/clusters?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cursor"] is None  # only 2 clusters
    ids = sorted(c["cluster_id"] for c in body["items"])
    assert ids == [1, 2]
    one = next(c for c in body["items"] if c["cluster_id"] == 1)
    assert one["size"] == 2
    assert one["max_score"] == 0.9


def test_cluster_detail(client):
    resp = client.get("/api/v1/runs/20260101_000000/clusters/1")
    body = resp.json()
    assert resp.status_code == 200
    assert sorted(body["row_ids"]) == [0, 1]
    assert len(body["pairs"]) == 1
    assert body["pairs"][0]["fields"][0]["field"] == "name"


def test_source_row(client):
    resp = client.get("/api/v1/runs/20260101_000000/rows/0")
    body = resp.json()
    assert body["columns"]["name"] == "Sony DSC-T77 Silver"
