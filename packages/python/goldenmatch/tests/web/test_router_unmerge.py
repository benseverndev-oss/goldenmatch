"""POST /api/v1/runs/{name}/unmerge — surgical cluster splits.

Mutates the saved run files in place (with .bak backup), so tests use
the sample_project tmp copy and verify both the wire response and the
post-mutation /clusters output.
"""
from __future__ import annotations


def test_unmerge_record_pulls_one_member_into_a_singleton(client):
    # The fixture run has a single 2-member cluster (rows 0, 1 in cluster 1).
    body = {"mode": "record", "cluster_id": 1, "row_id": 1}
    resp = client.post("/api/v1/runs/20260101_000000/unmerge", json=body)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["mode"] == "record"
    assert out["broken_pairs"] == 1

    # Cluster 1 should no longer contain row 1; row 1 lives in its own cluster.
    page = client.get("/api/v1/runs/20260101_000000/clusters?limit=20").json()
    by_id = {c["cluster_id"]: c for c in page["items"]}
    # The only multi-member cluster (1) should have been replaced; both
    # rows are now singletons.
    sizes = sorted(c["size"] for c in page["items"])
    assert all(s == 1 for s in sizes)


def test_unmerge_cluster_shatters_into_singletons(client):
    body = {"mode": "cluster", "cluster_id": 1}
    resp = client.post("/api/v1/runs/20260101_000000/unmerge", json=body)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["mode"] == "cluster"
    # Single 2-member cluster shattered → 1 broken pair.
    assert out["broken_pairs"] == 1

    page = client.get("/api/v1/runs/20260101_000000/clusters?limit=20").json()
    assert all(c["size"] == 1 for c in page["items"])


def test_unmerge_writes_backup_files(client, sample_project):
    client.post(
        "/api/v1/runs/20260101_000000/unmerge",
        json={"mode": "cluster", "cluster_id": 1},
    )
    bak_lineage = sample_project / "20260101_000000_lineage.json.bak"
    bak_clusters = sample_project / "20260101_000000_clusters.csv.bak"
    assert bak_lineage.exists()
    assert bak_clusters.exists()


def test_unmerge_404_on_unknown_cluster(client):
    resp = client.post(
        "/api/v1/runs/20260101_000000/unmerge",
        json={"mode": "cluster", "cluster_id": 9999},
    )
    assert resp.status_code == 404


def test_unmerge_record_400_when_row_not_in_cluster(client):
    resp = client.post(
        "/api/v1/runs/20260101_000000/unmerge",
        json={"mode": "record", "cluster_id": 1, "row_id": 99},
    )
    assert resp.status_code == 400
    assert "not in cluster" in resp.json()["detail"]


def test_unmerge_400_on_singleton_cluster(client):
    resp = client.post(
        "/api/v1/runs/20260101_000000/unmerge",
        json={"mode": "cluster", "cluster_id": 2},  # cluster 2 is a singleton
    )
    assert resp.status_code == 400
    assert "singleton" in resp.json()["detail"]
