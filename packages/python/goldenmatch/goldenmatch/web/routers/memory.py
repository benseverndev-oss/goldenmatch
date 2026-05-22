"""GET /api/v1/memory/{corrections,stats} + POST /api/v1/memory/learn.

Browses the Learning Memory SQLite store the workbench's labels are mirrored
into (see web/routers/labels.py::_mirror_to_memory_store). The default memory
path matches goldenmatch._api.get_memory's default (CWD-relative
``.goldenmatch/memory.db``) — same store the pipeline reads on every run.

The labels endpoint is the canonical write path for steward decisions; this
router is read + learn-only so the workbench's loop is closed end-to-end:
label → mirror → browse → learn → next run picks up adjustments.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from goldenmatch._api import get_memory, learn, memory_stats
from goldenmatch.core.memory.store import Correction

router = APIRouter(prefix="/api/v1/memory")

CORRECTIONS_CAP = 500


def _serialize_correction(c: Correction) -> dict[str, Any]:
    """Project a Correction dataclass into a JSON-safe dict.

    Drop ``field_hash`` / ``record_hash`` from the wire shape — they're
    workbench noise (the canonical anti-rot mechanism, irrelevant to the
    browsing UI) and they can dwarf the rest of the row in display weight.
    Datetimes serialize as ISO strings; raw enums as their value strings.
    """
    return {
        "id": c.id,
        "id_a": c.id_a,
        "id_b": c.id_b,
        "decision": str(c.decision),
        "source": str(c.source),
        "trust": c.trust,
        "original_score": c.original_score,
        "matchkey_name": c.matchkey_name,
        "reason": c.reason,
        "dataset": c.dataset,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        # v1.19.0 field-level Correction fields (#437)
        "field_name": getattr(c, "field_name", None),
        "original_value": getattr(c, "original_value", None),
        "corrected_value": getattr(c, "corrected_value", None),
    }


# v1.19.0 -- Phase 2: POST /corrections (#437 surface sync)


class CorrectionCreate(BaseModel):
    """Pair-level OR field-level correction request body.

    Pair-level requires `id_a` + `id_b` + `decision in {approve, reject}`.
    Field-level requires `cluster_id` + `field_name` + `corrected_value`
    + `decision="field_correct"`.

    `dataset` required for both. `source` defaults to 'rest' (trust 0.8).
    """
    decision: str = Field(..., description="approve | reject | field_correct")
    dataset: str
    id_a: int | None = None
    id_b: int | None = None
    cluster_id: int | None = None
    field_name: str | None = None
    original_value: str | None = None
    corrected_value: str | None = None
    reason: str | None = None
    matchkey_name: str | None = None
    source: str = Field(default="rest", description="Source tag")


@router.post("/corrections", status_code=201)
def create_correction(body: CorrectionCreate) -> dict[str, Any]:
    """File a pair-level or field-level Correction into Learning Memory."""
    from goldenmatch.core.memory.store import (
        HIGH_TRUST_SOURCES,
        _canon_pair,
    )
    from goldenmatch.core.memory.store import (
        Correction as CorrectionDC,
    )

    if body.decision not in ("approve", "reject", "field_correct"):
        raise HTTPException(
            400,
            detail=f"Invalid decision: {body.decision!r}",
        )
    if not body.dataset:
        raise HTTPException(400, detail="dataset is required")

    # Trust derived from source: HIGH_TRUST_SOURCES = 1.0, else 0.5.
    # REST defaults to 0.8 -- between agent (0.5) and human steward (1.0).
    if body.source == "rest":
        trust = 0.8
    elif body.source in {s.value for s in HIGH_TRUST_SOURCES}:
        trust = 1.0
    else:
        trust = 0.5

    if body.decision == "field_correct":
        if not body.field_name:
            raise HTTPException(400, detail="field_correct requires field_name")
        if body.corrected_value is None:
            raise HTTPException(
                400, detail="field_correct requires corrected_value",
            )
        cid = body.cluster_id if body.cluster_id is not None else (body.id_a or 0)
        correction = CorrectionDC(
            id=str(uuid.uuid4()),
            id_a=cid,
            id_b=0,
            decision=body.decision,
            source=body.source,
            trust=trust,
            field_hash="",
            record_hash="",
            original_score=0.0,
            matchkey_name=body.matchkey_name,
            reason=body.reason,
            dataset=body.dataset,
            created_at=datetime.now(UTC),
            field_name=body.field_name,
            original_value=body.original_value,
            corrected_value=body.corrected_value,
        )
    else:
        if body.id_a is None or body.id_b is None:
            raise HTTPException(
                400, detail=f"{body.decision} requires id_a and id_b",
            )
        ca, cb = _canon_pair(body.id_a, body.id_b)
        correction = CorrectionDC(
            id=str(uuid.uuid4()),
            id_a=ca,
            id_b=cb,
            decision=body.decision,
            source=body.source,
            trust=trust,
            field_hash="",
            record_hash="",
            original_score=0.0,
            matchkey_name=body.matchkey_name,
            reason=body.reason,
            dataset=body.dataset,
            created_at=datetime.now(UTC),
        )

    store = get_memory()
    try:
        store.add_correction(correction)
    finally:
        store.close()
    return _serialize_correction(correction)


@router.get("/corrections")
def list_corrections(
    dataset: str | None = Query(
        None,
        description="Filter to corrections for a specific dataset (e.g. project_root).",
    ),
    limit: int = Query(CORRECTIONS_CAP, ge=1, le=CORRECTIONS_CAP),
) -> dict:
    store = get_memory()
    try:
        items = store.get_corrections(dataset=dataset)
    finally:
        store.close()

    # Newest first — corrections accumulate over time and steward review wants
    # "what did I just decide" before historical decisions. A None created_at
    # sorts to the bottom; mixing datetime with int 0 would TypeError.
    _epoch = datetime.min.replace(tzinfo=UTC)
    items.sort(key=lambda c: c.created_at or _epoch, reverse=True)
    truncated = len(items) > limit
    return {
        "items": [_serialize_correction(c) for c in items[:limit]],
        "total": len(items),
        "truncated": truncated,
        "limit": limit,
    }


@router.get("/stats")
def stats() -> dict:
    raw = memory_stats()
    last = raw.get("last_learn_time")
    return {
        "count": raw.get("count", 0),
        "last_learn_time": last.isoformat() if last is not None else None,
        # Adjustments come back as plain dicts via __dict__; pass through.
        "adjustments": raw.get("adjustments", []),
    }


@router.post("/learn")
def trigger_learn(
    matchkey_name: str | None = Query(
        None,
        description="Restrict the learning pass to this matchkey only.",
    ),
) -> dict:
    """Run a learning pass over accumulated corrections.

    Threshold tuning fires when ≥10 corrections exist (LearningConfig
    default); weight learning at ≥50. Below those bars the pass returns
    no adjustments — the response surfaces that as ``adjustments=[]`` so
    the UI can show "not enough data yet" without a separate code path.
    """
    try:
        adjustments = learn(matchkey_name=matchkey_name)
    except Exception as exc:
        # Engine surfaces a few narrow failure modes here; map to 400 so the
        # UI can render the message rather than dumping a 500 trace.
        raise HTTPException(status_code=400, detail=f"learn failed: {exc}")
    return {
        "adjustments": [a.__dict__ for a in adjustments],
        "matchkey_filter": matchkey_name,
    }
