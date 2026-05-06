"""POST /api/v1/compare — CCMS comparison of two runs."""
from __future__ import annotations

import json
from pathlib import Path


def _write_second_run(project: Path, run_name: str, clusters_csv: str, pairs: list[dict]) -> None:
    """Drop a second run alongside the fixture run.

    The compare router only needs the clusters CSV. The lineage file is
    written so the run is discoverable via ``discover_runs`` (which
    requires both files to be present).
    """
    (project / f"{run_name}_clusters.csv").write_text(clusters_csv, encoding="utf-8")
    (project / f"{run_name}_lineage.json").write_text(
        json.dumps({
            "generated_at": "2026-01-02T00:00:00",
            "run_name": run_name,
            "total_pairs": len(pairs),
            "pairs": pairs,
        }),
        encoding="utf-8",
    )


def test_compare_unchanged_when_runs_match(client, sample_project: Path):
    # Mirror the baseline (rows 0,1 in cluster 1; row 2 in cluster 2) with
    # different cluster_ids — the comparator keys on member SETS, not IDs.
    _write_second_run(
        sample_project,
        "20260102_000000",
        "row_id,cluster_id\n0,5\n1,5\n2,9\n",
        [],
    )
    body = client.post(
        "/api/v1/compare",
        json={"run_a": "20260101_000000", "run_b": "20260102_000000"},
    ).json()
    s = body["summary"]
    assert s["unchanged"] == 2
    assert s["merged"] == 0
    assert s["partitioned"] == 0
    assert s["overlapping"] == 0
    assert s["cc1"] == 2
    assert s["cc2"] == 2
    assert body["run_a"] == "20260101_000000"
    assert body["run_b"] == "20260102_000000"
    assert len(body["cases"]) == 2


def test_compare_merged_when_b_pulls_clusters_together(client, sample_project: Path):
    # Run B merges {0,1} and {2} into a single cluster.
    _write_second_run(
        sample_project,
        "20260102_000000",
        "row_id,cluster_id\n0,1\n1,1\n2,1\n",
        [],
    )
    body = client.post(
        "/api/v1/compare",
        json={"run_a": "20260101_000000", "run_b": "20260102_000000"},
    ).json()
    s = body["summary"]
    # Both ER1 clusters are subsets of the single ER2 cluster → both "merged".
    assert s["merged"] == 2
    assert s["unchanged"] == 0


def test_compare_partitioned_when_b_splits_a_cluster(client, sample_project: Path):
    # Run B splits the {0,1} cluster into singletons.
    _write_second_run(
        sample_project,
        "20260102_000000",
        "row_id,cluster_id\n0,10\n1,11\n2,12\n",
        [],
    )
    body = client.post(
        "/api/v1/compare",
        json={"run_a": "20260101_000000", "run_b": "20260102_000000"},
    ).json()
    s = body["summary"]
    # Cluster {0,1} → two singletons in B = partitioned. Cluster {2} unchanged.
    assert s["partitioned"] == 1
    assert s["unchanged"] == 1


def test_compare_400_on_different_row_coverage(client, sample_project: Path):
    # Run B is missing row 2 → comparator raises ValueError.
    _write_second_run(
        sample_project,
        "20260102_000000",
        "row_id,cluster_id\n0,1\n1,1\n",
        [],
    )
    resp = client.post(
        "/api/v1/compare",
        json={"run_a": "20260101_000000", "run_b": "20260102_000000"},
    )
    assert resp.status_code == 400
    assert "different row IDs" in resp.json()["detail"]


def test_compare_overlapping_when_b_splits_clusters_across_each_other(
    client, sample_project: Path,
):
    """Overlapping = a B cluster contains some but not all members of an
    A cluster, AND those B clusters aren't strict subsets of A. Build it:
    A = {0,1}, {2}; B = {0,2}, {1}. B's cluster {0,2} intersects A's {0,1}
    AND A's {2} — neither subset, so cluster {0,1} → overlapping."""
    # Re-shape the fixture so A has two members in one cluster + one alone.
    # The default fixture already does that ({0,1} in cluster 1, {2} in 2).
    _write_second_run(
        sample_project,
        "20260102_000000",
        "row_id,cluster_id\n0,7\n1,8\n2,7\n",
        [],
    )
    body = client.post(
        "/api/v1/compare",
        json={"run_a": "20260101_000000", "run_b": "20260102_000000"},
    ).json()
    s = body["summary"]
    # Cluster {0,1} (A) → B's {0,2} and {1}: members 0 and 1 split across B
    # clusters that include row 2 (not in A's original {0,1}). Not subsets,
    # so overlapping. Cluster {2} (A) → B's {0,2}: subset (just {2})? No,
    # B's {0,2} contains an element not in A's {2}, so this is "merged"
    # from the perspective of A's {2} (single B cluster, but {0,2} ≠ {2}).
    assert s["overlapping"] == 1
    assert s["merged"] == 1


def test_compare_404_on_unknown_run(client):
    resp = client.post(
        "/api/v1/compare",
        json={"run_a": "20260101_000000", "run_b": "nope"},
    )
    assert resp.status_code == 404
