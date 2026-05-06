"""POST /api/v1/compare — CCMS comparison of two runs on the same dataset.

Wraps ``goldenmatch.core.compare_clusters.compare_clusters``. Each run's
clusters CSV (cluster_id, row_id) is reshaped into the engine's
``dict[int, {"members": [...]}]`` format and fed to the comparator.

The comparator REQUIRES both runs to cover identical row IDs. Two runs
on different inputs (different sample sizes, post-filter cuts, etc.)
surface as a 400 with a brief explanation rather than a 500.
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from goldenmatch.core.compare_clusters import compare_clusters
from goldenmatch.web import runs as runs_mod

router = APIRouter(prefix="/api/v1/compare")


class CompareRequest(BaseModel):
    run_a: str = Field(..., description="Baseline run name (ER1).")
    run_b: str = Field(..., description="Comparison run name (ER2).")


def _find_run(state, run_name: str):
    for ref in runs_mod.discover_runs(state.runs_dir or state.project_root):
        if ref.run_name == run_name:
            return ref
    if state.registry is not None:
        ref = state.registry.get(run_name)
        if ref is not None:
            return ref
    raise HTTPException(status_code=404, detail=f"run not found: {run_name}")


def _clusters_dict(ref) -> dict[int, dict]:
    df = runs_mod.load_clusters_df(ref)
    out: dict[int, list[int]] = {}
    for cid, rid in zip(df["cluster_id"].to_list(), df["row_id"].to_list()):
        out.setdefault(int(cid), []).append(int(rid))
    return {cid: {"members": members} for cid, members in out.items()}


@router.post("")
def compare(payload: CompareRequest, request: Request) -> dict:
    state = request.app.state.app_state
    ref_a = _find_run(state, payload.run_a)
    ref_b = _find_run(state, payload.run_b)

    clusters_a = _clusters_dict(ref_a)
    clusters_b = _clusters_dict(ref_b)

    try:
        result = compare_clusters(clusters_a, clusters_b)
    except ValueError as e:
        # Different row-ID coverage — engine raises ValueError. Surface as
        # 400 with the message intact so the UI can show the diagnostic.
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "run_a": payload.run_a,
        "run_b": payload.run_b,
        "summary": result.summary(),
        "cases": [asdict(c) for c in result.cases],
    }
