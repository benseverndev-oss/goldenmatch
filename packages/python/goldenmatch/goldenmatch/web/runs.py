from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import polars as pl


@dataclass(frozen=True)
class RunRef:
    run_name: str
    lineage_path: Path
    clusters_path: Path


@dataclass(frozen=True)
class RunManifest:
    run_name: str
    generated_at: str
    total_pairs: int
    cluster_count: int
    row_count: int


def discover_runs(project_dir: Path) -> list[RunRef]:
    """Return runs sorted newest first by lineage filename."""
    lineages = sorted(project_dir.glob("*_lineage.json"), reverse=True)
    out: list[RunRef] = []
    for lp in lineages:
        run_name = lp.stem.removesuffix("_lineage")
        cp = lp.with_name(f"{run_name}_clusters.csv")
        if cp.exists():
            out.append(RunRef(run_name=run_name, lineage_path=lp, clusters_path=cp))
    return out


def load_lineage(ref: RunRef) -> dict:
    return json.loads(ref.lineage_path.read_text(encoding="utf-8"))


def load_clusters_df(ref: RunRef) -> pl.DataFrame:
    return pl.read_csv(ref.clusters_path)


def load_run_manifest(ref: RunRef) -> RunManifest:
    lineage = load_lineage(ref)
    df = load_clusters_df(ref)
    return RunManifest(
        run_name=ref.run_name,
        generated_at=lineage.get("generated_at", ""),
        total_pairs=int(lineage.get("total_pairs", 0)),
        cluster_count=int(df.select(pl.col("cluster_id").n_unique()).item()),
        row_count=int(df.height),
    )


def _load_source_df(ref: RunRef) -> pl.DataFrame:
    """Locate and load the source CSV. v1 expects `data.csv` next to the run."""
    src = ref.lineage_path.parent / "data.csv"
    if not src.exists():
        raise FileNotFoundError(f"source CSV not found at {src}")
    return pl.read_csv(src)


def cluster_summaries(ref: RunRef) -> list[dict]:
    """Per-cluster size + score range, computed from clusters CSV + lineage.

    Recomputes per call (no cache). Acceptable up to ~10K clusters; revisit if
    the project endpoint becomes a hot path on large runs.
    """
    df = load_clusters_df(ref)
    lineage = load_lineage(ref)
    pairs_by_cluster: dict[int, list[float]] = {}
    for p in lineage.get("pairs", []):
        pairs_by_cluster.setdefault(int(p["cluster_id"]), []).append(float(p["score"]))

    summaries = []
    for cid, group in df.group_by("cluster_id"):
        cid_int = int(cid[0]) if isinstance(cid, tuple) else int(cid)
        scores = pairs_by_cluster.get(cid_int, [])
        # Deterministic representative: smallest row_id in the cluster.
        rep_row_id = int(group["row_id"].min())
        summaries.append({
            "cluster_id": cid_int,
            "size": int(group.height),
            "max_score": max(scores) if scores else None,
            "min_score": min(scores) if scores else None,
            "representative_row_id": rep_row_id,
        })
    summaries.sort(key=lambda c: c["cluster_id"])
    return summaries


def cluster_detail(ref: RunRef, cluster_id: int) -> dict:
    df = load_clusters_df(ref)
    lineage = load_lineage(ref)
    members = df.filter(pl.col("cluster_id") == cluster_id)
    if members.height == 0:
        raise KeyError(cluster_id)
    row_ids = [int(r) for r in members["row_id"]]
    src = _load_source_df(ref)
    rows = [{"row_id": rid, "columns": src.row(rid, named=True)} for rid in row_ids]
    pairs = [p for p in lineage.get("pairs", []) if int(p["cluster_id"]) == cluster_id]
    return {"cluster_id": cluster_id, "row_ids": row_ids, "rows": rows, "pairs": pairs}


def source_row(ref: RunRef, row_id: int) -> dict:
    src = _load_source_df(ref)
    if row_id < 0 or row_id >= src.height:
        raise IndexError(row_id)
    return {"row_id": row_id, "columns": src.row(row_id, named=True)}
