"""REST endpoints for the Identity Graph.

GET    /api/v1/identities                            -> list (paginated)
GET    /api/v1/identities/stats                      -> counts
GET    /api/v1/identities/{eid}                      -> full view
GET    /api/v1/identities/{eid}/history              -> events
GET    /api/v1/identities/{eid}/evidence             -> edges
GET    /api/v1/identities/by-record/{record_id}      -> view via record id
GET    /api/v1/identities/conflicts                  -> conflicting edges
POST   /api/v1/identities/{eid}/merge                -> manual merge
POST   /api/v1/identities/{eid}/split                -> manual split
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from goldenmatch.identity import (
    IdentityStore,
    find_by_record,
    find_conflicts,
    get_entity,
    history,
    list_entities,
    manual_merge,
    manual_split,
)

router = APIRouter(prefix="/api/v1/identities")


def _store_for(request: Request) -> IdentityStore:
    """Open the identity DB located at ``<project_root>/.goldenmatch/identity.db``.

    Single-tenant web tool -- one store per request, opened+closed inline so
    we don't hold a handle across the request lifecycle. The path is fixed
    relative to the project root for parity with MemoryStore behaviour.
    """
    state = request.app.state.app_state
    db_path = Path(state.project_root) / ".goldenmatch" / "identity.db"
    if not db_path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                "Identity graph not initialized. Run a dedupe pipeline with "
                "`identity.enabled: true` in goldenmatch.yml first."
            ),
        )
    return IdentityStore(path=str(db_path))


@router.get("/stats")
def stats(request: Request, dataset: str | None = Query(None)) -> dict[str, Any]:
    with _store_for(request) as s:
        return {
            "total": s.count_identities(),
            "by_dataset": s.count_identities(dataset=dataset) if dataset else None,
        }


@router.get("")
def list_endpoint(
    request: Request,
    dataset: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    with _store_for(request) as s:
        return {
            "items": list_entities(s, dataset=dataset, status=status, limit=limit, offset=offset),
            "limit": limit,
            "offset": offset,
        }


@router.get("/conflicts")
def conflicts_endpoint(
    request: Request, dataset: str | None = Query(None)
) -> dict[str, Any]:
    with _store_for(request) as s:
        return {"items": find_conflicts(s, dataset=dataset)}


@router.get("/by-record/{record_id:path}")
def by_record_endpoint(record_id: str, request: Request) -> dict[str, Any]:
    with _store_for(request) as s:
        view = find_by_record(s, record_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"No identity for record {record_id}")
    return view.to_dict()


@router.get("/{entity_id}")
def get_endpoint(entity_id: str, request: Request) -> dict[str, Any]:
    with _store_for(request) as s:
        view = get_entity(s, entity_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"Identity {entity_id} not found")
    return view.to_dict()


@router.get("/{entity_id}/history")
def history_endpoint(
    entity_id: str, request: Request, limit: int = Query(100, ge=1, le=1000)
) -> dict[str, Any]:
    with _store_for(request) as s:
        return {"items": history(s, entity_id, limit=limit)}


@router.get("/{entity_id}/evidence")
def evidence_endpoint(entity_id: str, request: Request) -> dict[str, Any]:
    with _store_for(request) as s:
        view = get_entity(s, entity_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"Identity {entity_id} not found")
    return {"items": view.to_dict()["edges"]}


class MergeRequest(BaseModel):
    absorb_entity_id: str
    reason: str | None = None


@router.post("/{entity_id}/merge")
def merge_endpoint(
    entity_id: str, body: MergeRequest, request: Request
) -> dict[str, Any]:
    with _store_for(request) as s:
        try:
            return manual_merge(
                s, keep_entity_id=entity_id,
                absorb_entity_id=body.absorb_entity_id,
                reason=body.reason,
                run_name="web",
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e


class SplitRequest(BaseModel):
    record_ids: list[str]
    reason: str | None = None


@router.post("/{entity_id}/split")
def split_endpoint(
    entity_id: str, body: SplitRequest, request: Request
) -> dict[str, Any]:
    with _store_for(request) as s:
        try:
            return manual_split(
                s, entity_id=entity_id, record_ids=body.record_ids,
                reason=body.reason, run_name="web",
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
