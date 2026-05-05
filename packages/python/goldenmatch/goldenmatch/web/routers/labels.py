from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from goldenmatch.web.labels import append_label, read_labels_dedup

router = APIRouter(prefix="/api/v1/labels")


class LabelIn(BaseModel):
    row_id_a: int
    row_id_b: int
    label: Literal["match", "non_match"]
    note: str | None = None


@router.post("")
def post_label(payload: LabelIn, request: Request) -> dict:
    state = request.app.state.app_state
    return append_label(state.labels_path, payload.model_dump())


@router.get("")
def list_labels(request: Request) -> list[dict]:
    state = request.app.state.app_state
    return read_labels_dedup(state.labels_path)
