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

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query

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
    }


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
    # "what did I just decide" before historical decisions.
    items.sort(key=lambda c: c.created_at or 0, reverse=True)
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
