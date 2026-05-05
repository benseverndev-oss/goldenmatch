from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query, Request

from goldenmatch.web import runs as runs_mod

router = APIRouter(prefix="/api/v1/runs")


def _find_run(state, run_name: str):
    for ref in runs_mod.discover_runs(state.runs_dir or state.project_root):
        if ref.run_name == run_name:
            return ref
    # In-memory preview registry (Task 5) sits alongside on-disk runs and
    # uses the same /api/v1/runs/{name} surface.
    if state.registry is not None:
        ref = state.registry.get(run_name)
        if ref is not None:
            return ref
    raise HTTPException(status_code=404, detail=f"run not found: {run_name}")


@router.get("/{run_name}")
def manifest(run_name: str, request: Request):
    ref = _find_run(request.app.state.app_state, run_name)
    return asdict(runs_mod.load_run_manifest(ref))


@router.get("/{run_name}/clusters")
def clusters(
    run_name: str,
    request: Request,
    cursor: int | None = Query(None, ge=0, description="Offset into the cluster list (0-based)."),
    limit: int = Query(50, ge=1, le=500, description="Page size."),
):
    """Paginated cluster summaries.

    Returns ``{items, cursor, total}``. Pass ``cursor`` from the previous response
    to fetch the next page; ``cursor=null`` indicates the end of the list.
    """
    ref = _find_run(request.app.state.app_state, run_name)
    summaries = runs_mod.cluster_summaries(ref)
    start = cursor or 0
    page = summaries[start : start + limit]
    next_cursor = start + limit if start + limit < len(summaries) else None
    return {"items": page, "cursor": next_cursor, "total": len(summaries)}


@router.get("/{run_name}/clusters/{cluster_id}")
def detail(run_name: str, cluster_id: int, request: Request):
    ref = _find_run(request.app.state.app_state, run_name)
    try:
        return runs_mod.cluster_detail(ref, cluster_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"cluster {cluster_id} not in run")


@router.get("/{run_name}/rows/{row_id}")
def row(run_name: str, row_id: int, request: Request):
    ref = _find_run(request.app.state.app_state, run_name)
    try:
        return runs_mod.source_row(ref, row_id)
    except IndexError:
        raise HTTPException(status_code=404, detail=f"row {row_id} out of range")
