from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

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


class SampleSpec(BaseModel):
    n: int = Field(gt=0, le=10000)
    seed: int = 0


class PreviewRequest(BaseModel):
    rules: RulesPayload
    sample: SampleSpec


@router.post("/preview")
async def preview(payload: PreviewRequest, request: Request) -> dict:
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
    return {"run_name": ref.run_name}
