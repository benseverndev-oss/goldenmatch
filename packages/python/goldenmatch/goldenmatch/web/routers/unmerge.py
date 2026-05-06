"""POST /api/v1/runs/{name}/unmerge — surgically split a saved run's clusters.

The engine already implements ``unmerge_record(record_id, clusters)`` (and
``unmerge_cluster(cluster_id, clusters)``) — they mutate a clusters dict
in place and return the updated mapping. This route wraps that for the
web UI: reconstruct the clusters dict from the saved run on disk, apply
the operation, write the updated lineage + clusters CSV back, AND mirror
the steward decision to MemoryStore so the NEXT run honors it.

Two operations:

  - ``mode="record"``: pull a single ``row_id`` out of its cluster. The
    other members re-cluster among themselves using stored pair_scores;
    the removed record becomes a singleton.
  - ``mode="cluster"``: shatter the cluster — every member becomes a
    singleton. Useful for "this whole group is wrong."

Mutation safety: ``{run_name}_lineage.json`` and ``{run_name}_clusters.csv``
are backed up to ``.bak`` siblings before write, atomic-write via tmp +
``os.replace``. Same hygiene as POST /rules/save.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from goldenmatch.core.cluster import unmerge_cluster, unmerge_record
from goldenmatch.web import runs as runs_mod

router = APIRouter(prefix="/api/v1/runs")


class UnmergeRequest(BaseModel):
    mode: Literal["record", "cluster"]
    cluster_id: int
    row_id: int | None = None  # required when mode == "record"


def _find_run(state, run_name: str):
    for ref in runs_mod.discover_runs(state.runs_dir or state.project_root):
        if ref.run_name == run_name:
            return ref
    if state.registry is not None:
        ref = state.registry.get(run_name)
        if ref is not None:
            return ref
    raise HTTPException(status_code=404, detail=f"run not found: {run_name}")


def _reconstruct_clusters(ref) -> dict[int, dict]:
    """Rebuild the engine-shape clusters dict from a saved run's files.

    Members come from clusters.csv (row_id → cluster_id). pair_scores come
    from lineage.json's pair list (canonicalized to (min, max)). The result
    matches what the pipeline produced in memory the day this run was saved
    — close enough for unmerge to operate on.
    """
    df = runs_mod.load_clusters_df(ref)
    lineage = runs_mod.load_lineage(ref)

    members_by_cid: dict[int, list[int]] = {}
    for row in df.iter_rows(named=True):
        cid = int(row["cluster_id"])
        members_by_cid.setdefault(cid, []).append(int(row["row_id"]))

    pairs_by_cid: dict[int, dict[tuple[int, int], float]] = {
        cid: {} for cid in members_by_cid
    }
    for p in lineage.get("pairs", []):
        cid = int(p["cluster_id"])
        a, b = int(p["row_id_a"]), int(p["row_id_b"])
        key = (a, b) if a <= b else (b, a)
        pairs_by_cid.setdefault(cid, {})[key] = float(p["score"])

    out: dict[int, dict] = {}
    for cid, members in members_by_cid.items():
        out[cid] = {
            "members": sorted(members),
            "size": len(members),
            "oversized": False,
            "pair_scores": pairs_by_cid.get(cid, {}),
            "confidence": 1.0,
            "bottleneck_pair": None,
            "cluster_quality": "strong",
        }
    return out


def _write_back(ref, clusters: dict[int, dict], project_root: Path) -> None:
    """Persist the updated clusters dict back to disk, atomically + with .bak."""
    # Backup
    for src in (ref.clusters_path, ref.lineage_path):
        if src.exists():
            shutil.copy2(src, src.with_suffix(src.suffix + ".bak"))

    # New clusters CSV
    csv_lines = ["row_id,cluster_id"]
    rid_to_cid: dict[int, int] = {}
    for cid, cinfo in clusters.items():
        for member in cinfo.get("members", []):
            rid_to_cid[int(member)] = int(cid)
    for rid in sorted(rid_to_cid):
        csv_lines.append(f"{rid},{rid_to_cid[rid]}")
    tmp_clusters = ref.clusters_path.with_suffix(".csv.tmp")
    tmp_clusters.write_text("\n".join(csv_lines) + "\n", encoding="utf-8")
    os.replace(tmp_clusters, ref.clusters_path)

    # Update lineage: drop pairs whose endpoints are no longer in the same
    # cluster. The original lineage records still describe what the engine
    # SAW; what we surface in the UI must match the new clusters.
    lineage = runs_mod.load_lineage(ref)
    kept_pairs = []
    for p in lineage.get("pairs", []):
        a, b = int(p["row_id_a"]), int(p["row_id_b"])
        cid_a, cid_b = rid_to_cid.get(a), rid_to_cid.get(b)
        if cid_a is not None and cid_a == cid_b:
            kept_pairs.append({**p, "cluster_id": cid_a})
    lineage["pairs"] = kept_pairs
    lineage["total_pairs"] = len(kept_pairs)
    tmp_lineage = ref.lineage_path.with_suffix(".json.tmp")
    tmp_lineage.write_text(json.dumps(lineage, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_lineage, ref.lineage_path)


def _record_steward_corrections(
    pairs: list[tuple[int, int]],
    project_root: Path,
    decision: str = "reject",
) -> None:
    """Mirror unmerge to MemoryStore so the next run honors the split."""
    try:
        from goldenmatch._api import add_correction
        for a, b in pairs:
            add_correction(
                id_a=a,
                id_b=b,
                decision=decision,
                source="steward",
                reason="web UI unmerge",
                dataset=str(project_root),
            )
    except Exception:
        # Don't fail the HTTP write — labels.jsonl + the file mutation
        # are the user-visible truth; memory is a durability optimization.
        pass


@router.post("/{run_name}/unmerge")
def post_unmerge(run_name: str, payload: UnmergeRequest, request: Request) -> dict:
    state = request.app.state.app_state
    ref = _find_run(state, run_name)
    clusters = _reconstruct_clusters(ref)

    if payload.cluster_id not in clusters:
        raise HTTPException(
            status_code=404,
            detail=f"cluster {payload.cluster_id} not in run {run_name}",
        )

    pairs_to_reject: list[tuple[int, int]] = []

    if payload.mode == "record":
        if payload.row_id is None:
            raise HTTPException(
                status_code=400,
                detail="row_id is required when mode='record'",
            )
        members = clusters[payload.cluster_id]["members"]
        if payload.row_id not in members:
            raise HTTPException(
                status_code=400,
                detail=f"row {payload.row_id} not in cluster {payload.cluster_id}",
            )
        if len(members) <= 1:
            raise HTTPException(
                status_code=400,
                detail="cluster is already a singleton — nothing to unmerge",
            )
        # Pairs being broken: (row_id, m) for every other member. The
        # MemoryStore correction asserts these pairs as non-matches so the
        # next run doesn't re-merge them.
        pairs_to_reject = [
            (min(payload.row_id, m), max(payload.row_id, m))
            for m in members
            if m != payload.row_id
        ]
        clusters = unmerge_record(payload.row_id, clusters)
    else:
        members = clusters[payload.cluster_id]["members"]
        if len(members) <= 1:
            raise HTTPException(
                status_code=400,
                detail="cluster is already a singleton — nothing to unmerge",
            )
        # Shatter: every pair within the cluster becomes a non-match.
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pairs_to_reject.append((members[i], members[j]))
        clusters = unmerge_cluster(payload.cluster_id, clusters)

    _write_back(ref, clusters, state.project_root)
    _record_steward_corrections(pairs_to_reject, state.project_root)

    return {
        "run_name": run_name,
        "mode": payload.mode,
        "broken_pairs": len(pairs_to_reject),
        "cluster_count": len(clusters),
    }
