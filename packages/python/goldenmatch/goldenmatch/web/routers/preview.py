from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import polars as pl
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from goldenmatch.config.schemas import RulesPayload
from goldenmatch.web.preview import run_preview

router = APIRouter(prefix="/api/v1")

PREVIEW_TIMEOUT_S = 30
# Single-worker by design: serializes preview runs across the whole process so
# concurrent requests can't fight over engine state. Acceptable for v1 (single-
# dev localhost). Revisit if/when async preview lands.
_executor = ThreadPoolExecutor(max_workers=1)

# v1 preview supports stringly-comparable scorers only. Embedding scorers
# require model bootstrap (HF download / Vertex creds) that's out of scope —
# reject early with a clear 400 instead of an opaque timeout / 500.
_UNSUPPORTED_SCORERS = frozenset({"embedding", "record_embedding"})


class SampleSpec(BaseModel):
    n: int = Field(gt=0, le=10000)
    seed: int = 0


class PreviewRequest(BaseModel):
    rules: RulesPayload
    sample: SampleSpec


def _reject_unsupported_scorers(rules: RulesPayload) -> None:
    bad = sorted({m.scorer for m in rules.matchkeys if m.scorer in _UNSUPPORTED_SCORERS})
    if bad:
        raise HTTPException(
            status_code=400,
            detail=(
                f"preview does not support scorer(s) {bad} in v1 — they need an "
                "embedding model the local server doesn't bootstrap. Run the full "
                "pipeline via `goldenmatch dedupe` for these."
            ),
        )


@router.post("/preview")
async def preview(payload: PreviewRequest, request: Request) -> dict:
    _reject_unsupported_scorers(payload.rules)
    state = request.app.state.app_state
    loop = asyncio.get_running_loop()
    try:
        ref = await asyncio.wait_for(
            loop.run_in_executor(
                _executor,
                lambda: run_preview(
                    project_root=state.project_root,
                    rules=payload.rules,
                    sample_n=payload.sample.n,
                    seed=payload.sample.seed,
                    registry=state.registry,
                ),
            ),
            timeout=PREVIEW_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=408,
            detail=f"preview exceeded {PREVIEW_TIMEOUT_S}s; lower sample size",
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (pl.exceptions.ColumnNotFoundError, KeyError, ValueError) as exc:
        # Most common workbench mistake: a matchkey references a column that
        # doesn't exist in data.csv. Surface the engine's message verbatim.
        raise HTTPException(status_code=400, detail=f"preview failed: {exc}")
    return {"run_name": ref.run_name}
