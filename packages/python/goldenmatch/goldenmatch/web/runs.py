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
