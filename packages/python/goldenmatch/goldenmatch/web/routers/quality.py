"""GET /api/v1/quality — scan data.csv via GoldenCheck (no fixes applied).

Wraps ``goldenmatch.core.quality._scan_only`` so the workbench can warn the
user when the source has data-quality issues that will hurt match accuracy
before they bother running the pipeline. GoldenCheck is an optional dep — if
it's not installed, the route returns ``available=false`` rather than 500ing,
so the frontend can render a friendly "install goldencheck for quality
findings" badge instead of breaking.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import polars as pl
from fastapi import APIRouter, HTTPException, Query, Request

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

QUALITY_TIMEOUT_S = 60
_executor = ThreadPoolExecutor(max_workers=1)


def _execute_scan(project_root: Path, domain: str | None) -> dict[str, Any]:
    src = project_root / "data.csv"
    if not src.exists():
        raise FileNotFoundError("source CSV (data.csv) not found in project root")

    from goldenmatch.core.quality import _goldencheck_available, _scan_only

    if not _goldencheck_available():
        return {
            "available": False,
            "issues": [],
            "summary": {"errors": 0, "warnings": 0, "total": 0},
        }

    df = pl.read_csv(src)
    try:
        _, issues = _scan_only(df, mode="announced", domain=domain)
        scan_error: str | None = None
    except Exception as exc:
        # _scan_only depends on goldencheck internals (Finding.rule_id, etc.);
        # version drift between goldencheck and goldenmatch can break this
        # without warning. Surface as a soft "scan failed" so the workbench
        # banner still renders (saying "couldn't scan") rather than 500ing.
        # Always log so server-side audit trails capture the type even when
        # the UI suppresses the trace.
        issues = []
        scan_error = f"{type(exc).__name__}: {exc}"
        log.warning("quality scan failed: %s", scan_error)

    errors = sum(1 for i in issues if (i.get("severity") or "").lower() == "error")
    warnings = sum(1 for i in issues if (i.get("severity") or "").lower() == "warning")
    out: dict[str, Any] = {
        "available": True,
        "issues": issues,
        "summary": {
            "errors": errors,
            "warnings": warnings,
            "total": len(issues),
        },
    }
    if scan_error is not None:
        out["error"] = scan_error
    return out


@router.get("/quality")
async def quality(
    request: Request,
    domain: str | None = Query(
        None,
        description="Optional GoldenCheck domain hint (healthcare, finance, ecommerce).",
    ),
) -> dict:
    state = request.app.state.app_state
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                _executor,
                lambda: _execute_scan(state.project_root, domain),
            ),
            timeout=QUALITY_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=408, detail=f"quality scan exceeded {QUALITY_TIMEOUT_S}s",
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
