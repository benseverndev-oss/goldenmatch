from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from goldenmatch.core.frame import to_frame as _to_frame


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


def load_clusters_df(ref: RunRef):
    # Arrow-native read (the W1 parity-pinned reader); returns pa.Table.
    from goldenmatch.core.io_arrow import read_table_arrow

    return read_table_arrow(ref.clusters_path)


def load_run_manifest(ref: RunRef) -> RunManifest:
    lineage = load_lineage(ref)
    df = load_clusters_df(ref)
    return RunManifest(
        run_name=ref.run_name,
        generated_at=lineage.get("generated_at", ""),
        total_pairs=int(lineage.get("total_pairs", 0)),
        cluster_count=int(_to_frame(df).column("cluster_id").n_unique()),
        row_count=int(_to_frame(df).height),
    )


def _load_source_df(ref: RunRef):
    """Locate and load the source CSV (arrow-native; returns pa.Table).
    v1 expects `data.csv` next to the run."""
    src = ref.lineage_path.parent / "data.csv"
    if not src.exists():
        raise FileNotFoundError(f"source CSV not found at {src}")
    from goldenmatch.core.io_arrow import read_table_arrow

    return read_table_arrow(src)


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
    for cid, group in _to_frame(df).group_partitions("cluster_id"):
        cid_int = int(cid[0]) if isinstance(cid, tuple) else int(cid)
        scores = pairs_by_cluster.get(cid_int, [])
        # Deterministic representative: smallest row_id in the cluster.
        rep_row_id = int(min(group.column("row_id").to_list()))
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
    members = _to_frame(df).filter_eq("cluster_id", cluster_id)
    if members.height == 0:
        raise KeyError(cluster_id)
    row_ids = [int(r) for r in members.column("row_id").to_list()]
    src = _to_frame(_load_source_df(ref))
    _src_rows = src.select_dicts(list(src.columns))
    rows = [{"row_id": rid, "columns": _src_rows[rid]} for rid in row_ids]
    pairs = [p for p in lineage.get("pairs", []) if int(p["cluster_id"]) == cluster_id]
    return {"cluster_id": cluster_id, "row_ids": row_ids, "rows": rows, "pairs": pairs}


def source_row(ref: RunRef, row_id: int) -> dict:
    src = _to_frame(_load_source_df(ref))
    if row_id < 0 or row_id >= src.height:
        raise IndexError(row_id)
    row = src.slice(row_id, 1).select_dicts(list(src.columns))[0]
    return {"row_id": row_id, "columns": row}


def lineage_pair_keys(ref: RunRef) -> set[tuple[int, int]]:
    """Set of canonical (min, max) pair keys present in this run's lineage.

    Used to scope labels to "pairs that appeared in this run." Canonicalized
    so it joins cleanly with the labels store (which also canonicalizes via
    web/labels.py::_canonical_pair).
    """
    lineage = load_lineage(ref)
    out: set[tuple[int, int]] = set()
    for p in lineage.get("pairs", []):
        a, b = int(p["row_id_a"]), int(p["row_id_b"])
        out.add((a, b) if a <= b else (b, a))
    return out
