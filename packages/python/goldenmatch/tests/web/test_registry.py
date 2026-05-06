from __future__ import annotations

from pathlib import Path

from goldenmatch.web.registry import PreviewRegistry


def test_registry_evicts_oldest(tmp_path: Path):
    reg = PreviewRegistry(max_entries=2)
    reg.put("a", lineage={"pairs": []}, clusters_csv="row_id,cluster_id\n0,1\n", source_csv="x")
    reg.put("b", lineage={"pairs": []}, clusters_csv="row_id,cluster_id\n0,1\n", source_csv="x")
    reg.put("c", lineage={"pairs": []}, clusters_csv="row_id,cluster_id\n0,1\n", source_csv="x")
    assert reg.get("a") is None
    assert reg.get("b") is not None
    assert reg.get("c") is not None
