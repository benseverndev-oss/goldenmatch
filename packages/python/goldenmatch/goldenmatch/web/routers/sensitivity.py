"""POST /api/v1/sensitivity — sweep one parameter, compare each point to baseline.

Wraps ``goldenmatch.core.sensitivity.run_sensitivity``. The engine reruns the
full pipeline at each sweep value, then CCMS-compares the resulting clusters
against a baseline run. Output gives the UI two things:

  1. A sparkline-friendly list of points: ``{value, cluster_count, twi, ...}``
     showing how the clustering shifts as the parameter moves.
  2. A ``stability_report`` flagging the value with the most unchanged
     clusters — a "where is this rule least sensitive" pointer.

Sample size matters: full-data sweeps are seconds-to-minutes per point. The
router caps ``sample_n`` and runs the work on the threadpool with a
RUN_TIMEOUT_S guard so the UI gets a 408 instead of a hang.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from goldenmatch.config.schemas import RulesPayload
from goldenmatch.core.sensitivity import SweepParam, run_sensitivity

router = APIRouter(prefix="/api/v1")

RUN_TIMEOUT_S = 300
_executor = ThreadPoolExecutor(max_workers=1)


class SensitivityRequest(BaseModel):
    field: str = Field(
        ...,
        description=(
            "Sweep field. Supported: 'threshold', 'blocking.max_block_size', "
            "'matchkey.<name>.threshold'."
        ),
    )
    start: float
    stop: float
    step: float = Field(..., gt=0)
    sample_n: int = Field(500, ge=10, le=10_000)
    rules: RulesPayload | None = None


def _execute_sweep(
    *,
    project_root: Path,
    rules: RulesPayload,
    field: str,
    start: float,
    stop: float,
    step: float,
    sample_n: int,
) -> dict[str, Any]:
    src = project_root / "data.csv"
    if not src.exists():
        raise FileNotFoundError("source CSV (data.csv) not found in project root")

    # Reuse the workbench → engine translation so OR-semantics across matchkey
    # rows match what `/preview` and `/run` produce. Without this, sweeping
    # `threshold` against a different config than the one the user is actually
    # running would mean the curve doesn't reflect their workbench state.
    from goldenmatch.web.preview import _build_config
    config = _build_config(rules)

    param = SweepParam(field=field, start=start, stop=stop, step=step)
    results = run_sensitivity(
        file_specs=[(str(src), "data")],
        config=config,
        sweep_params=[param],
        sample_size=sample_n,
    )
    if not results:
        return {"field": field, "baseline_value": None, "stability": {}, "points": []}

    res = results[0]
    points = []
    for p in res.points:
        points.append({
            "value": p.param_value,
            "cluster_count_a": p.comparison.cc1,
            "cluster_count_b": p.comparison.cc2,
            "unchanged": p.comparison.unchanged,
            "merged": p.comparison.merged,
            "partitioned": p.comparison.partitioned,
            "overlapping": p.comparison.overlapping,
            "twi": round(p.comparison.twi, 4),
        })
    return {
        "field": field,
        "baseline_value": res.baseline_value,
        "stability": res.stability_report(),
        "points": points,
        "sample_n": sample_n,
    }


@router.post("/sensitivity")
async def sensitivity(payload: SensitivityRequest, request: Request) -> dict:
    state = request.app.state.app_state
    rules = payload.rules or state.rules
    if rules is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "no rules to sweep — pass `rules` in the body or first edit / "
                "autoconfig the rules via the workbench."
            ),
        )

    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                _executor,
                lambda: _execute_sweep(
                    project_root=state.project_root,
                    rules=rules,
                    field=payload.field,
                    start=payload.start,
                    stop=payload.stop,
                    step=payload.step,
                    sample_n=payload.sample_n,
                ),
            ),
            timeout=RUN_TIMEOUT_S,
        )
    except TimeoutError:
        raise HTTPException(
            status_code=408,
            detail=f"sensitivity sweep exceeded {RUN_TIMEOUT_S}s — try fewer points or a smaller sample",
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        # Engine raises ValueError for unsupported sweep fields and unknown
        # matchkey names — surface the message intact to the UI.
        raise HTTPException(status_code=400, detail=str(exc))
