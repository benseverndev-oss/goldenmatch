from __future__ import annotations

from pathlib import Path

from goldenmatch.web.runs import discover_runs, load_run_manifest, load_lineage


def test_discover_runs(sample_project: Path) -> None:
    runs = discover_runs(sample_project)
    assert [r.run_name for r in runs] == ["20260101_000000"]
    assert runs[0].lineage_path.name == "20260101_000000_lineage.json"
    assert runs[0].clusters_path.name == "20260101_000000_clusters.csv"


def test_load_manifest(sample_project: Path) -> None:
    runs = discover_runs(sample_project)
    m = load_run_manifest(runs[0])
    assert m.cluster_count == 2
    assert m.row_count == 3
    assert m.total_pairs == 1


def test_load_lineage(sample_project: Path) -> None:
    runs = discover_runs(sample_project)
    lineage = load_lineage(runs[0])
    assert lineage["pairs"][0]["row_id_a"] == 0
