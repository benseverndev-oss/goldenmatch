from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, model_validator

from goldenmatch.web.labels import append_label, read_labels_dedup

router = APIRouter(prefix="/api/v1/labels")
log = logging.getLogger(__name__)


class LabelIn(BaseModel):
    row_id_a: int
    row_id_b: int
    label: Literal["match", "non_match"]
    note: str | None = None

    @model_validator(mode="after")
    def _reject_self_pair(self) -> LabelIn:
        if self.row_id_a == self.row_id_b:
            raise ValueError("row_id_a and row_id_b must differ (self-pair has no meaning)")
        return self


def _mirror_to_memory_store(payload: LabelIn, project_root) -> tuple[bool, str | None]:
    """Mirror a steward label into goldenmatch's Learning Memory store.

    The labels.jsonl is the workbench's steward-facing record of decisions;
    MemoryStore is the canonical store the matching pipeline reads on every
    run via ``apply_corrections`` (CLAUDE.md: "v1.6.0 Learning Memory:
    end-to-end loop wired"). Without this mirror, web labels would be
    cosmetic — they'd appear in the inspector tab but the next "Run for
    real" would re-discover the same false positives the user just rejected.

    Caveat: ``add_correction`` writes empty ``record_hash``, so the
    re-anchoring path falls back to row-ID presence (CLAUDE.md: "empty-hash
    entries via the row-ID-presence path"). This works reliably for full-
    data runs (deterministic ``__row_id__``) but NOT for sampled previews,
    where row IDs depend on sample order. The mirror is durable for runs
    started via "Run for real"; preview-only labels may not re-apply.

    Failures here log but don't block the HTTP write to labels.jsonl —
    the steward record is the source of truth for the UI.

    Returns ``(mirrored, error)`` so the route can surface mirror-fall-
    through to the UI (yellow toast / banner) rather than silently
    succeeding while the next pipeline run rediscovers the same wrong
    decision.
    """
    try:
        from goldenmatch._api import add_correction
        decision = "merge" if payload.label == "match" else "reject"
        add_correction(
            id_a=payload.row_id_a,
            id_b=payload.row_id_b,
            decision=decision,
            source="steward",  # trust=1.0 — this is a human gating action
            reason=payload.note,
            dataset=str(project_root),
        )
        return True, None
    except Exception as exc:  # MemoryStore can fail if backend unwritable etc.
        msg = f"{type(exc).__name__}: {exc}"
        log.warning("label mirror to MemoryStore failed: %s", msg)
        return False, msg


@router.post("")
def post_label(payload: LabelIn, request: Request) -> dict:
    state = request.app.state.app_state
    record = append_label(state.labels_path, payload.model_dump())
    mirrored, mirror_error = _mirror_to_memory_store(payload, state.project_root)
    record["mirrored"] = mirrored
    if mirror_error is not None:
        record["mirror_error"] = mirror_error
    return record


@router.get("")
def list_labels(request: Request) -> list[dict]:
    state = request.app.state.app_state
    return read_labels_dedup(state.labels_path)
