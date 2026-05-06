"""POST /api/v1/match — one-to-many target × reference workflow.

Distinct shape from dedupe (which has its own /run + /preview surface):

  - Two CSVs: a target dataset and a reference dataset, both relative to
    project_root. The route resolves both paths under project_root to keep
    the surface tied to the project the user is exploring.
  - Output is target → top-1 reference match (the engine's "best" mode):
    a flat row carrying `__target_row_id__`, `__ref_row_id__`,
    `__match_score__`, and `target_*` / `ref_*` columns alongside the
    matched fields. Plus the unmatched targets so the UI can show coverage.

Saved-run artifacts (lineage / clusters CSV) aren't produced — match runs
don't have clusters, and forcing the dedupe shape here would invent
identity claims the engine never made.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import polars as pl
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from goldenmatch.config.schemas import GoldenMatchConfig, RulesPayload
from goldenmatch.core.pipeline import run_match_df

router = APIRouter(prefix="/api/v1")

MATCH_TIMEOUT_S = 300
ROW_CAP = 500
_executor = ThreadPoolExecutor(max_workers=1)


class MatchRequest(BaseModel):
    reference_path: str = Field(
        ...,
        description="Reference CSV path relative to project_root.",
    )
    target_path: str = Field(
        "data.csv",
        description="Target CSV path relative to project_root. Defaults to data.csv.",
    )
    auto_config: bool = False
    rules: RulesPayload | None = None


def _safe_resolve(project_root: Path, rel: str) -> Path:
    """Resolve `rel` under `project_root` and reject anything that escapes it.

    Path traversal guard — without this, the workbench would happily read any
    file readable by the server process. Single-tenant localhost or not, this
    is a cheap correctness check.
    """
    candidate = (project_root / rel).resolve()
    try:
        candidate.relative_to(project_root.resolve())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"path '{rel}' escapes the project root",
        )
    if not candidate.exists():
        raise HTTPException(status_code=400, detail=f"path '{rel}' not found in project")
    return candidate


def _execute_match(
    *,
    project_root: Path,
    target_path: str,
    reference_path: str,
    auto_config: bool,
    rules: RulesPayload | None,
) -> dict[str, Any]:
    target_full = _safe_resolve(project_root, target_path)
    ref_full = _safe_resolve(project_root, reference_path)

    target_df = pl.read_csv(target_full)
    ref_df = pl.read_csv(ref_full)

    config: GoldenMatchConfig
    if auto_config:
        config = GoldenMatchConfig()
    else:
        if rules is None:
            raise HTTPException(
                status_code=400,
                detail="rules required when auto_config is false",
            )
        from goldenmatch.web.preview import _build_config
        config = _build_config(rules)

    result = run_match_df(target_df, ref_df, config, auto_config=auto_config)

    matched: pl.DataFrame | None = result.get("matched")
    unmatched: pl.DataFrame | None = result.get("unmatched")

    matched_rows = (
        matched.head(ROW_CAP).to_dicts() if matched is not None else []
    )
    unmatched_rows = (
        unmatched.head(ROW_CAP).to_dicts() if unmatched is not None else []
    )

    matched_total = matched.height if matched is not None else 0
    unmatched_total = unmatched.height if unmatched is not None else 0
    target_total = target_df.height
    matched_targets = (
        len(set(matched["__target_row_id__"].to_list())) if matched is not None else 0
    )

    return {
        "stats": {
            "target_total": target_total,
            "reference_total": ref_df.height,
            "matched_pairs": matched_total,
            "matched_targets": matched_targets,
            "unmatched_targets": unmatched_total,
            "match_rate": round(matched_targets / target_total, 4) if target_total else 0.0,
        },
        "matched": matched_rows,
        "unmatched": unmatched_rows,
        "row_cap": ROW_CAP,
        "matched_truncated": matched_total > ROW_CAP,
        "unmatched_truncated": unmatched_total > ROW_CAP,
    }


@router.post("/match")
async def match_run(payload: MatchRequest, request: Request) -> dict:
    state = request.app.state.app_state
    rules = payload.rules or state.rules
    if not payload.auto_config and rules is None:
        raise HTTPException(
            status_code=400,
            detail="no rules to match with — pass `rules`, set auto_config=true, or edit rules in the workbench first.",
        )

    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                _executor,
                lambda: _execute_match(
                    project_root=state.project_root,
                    target_path=payload.target_path,
                    reference_path=payload.reference_path,
                    auto_config=payload.auto_config,
                    rules=None if payload.auto_config else rules,
                ),
            ),
            timeout=MATCH_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=408,
            detail=f"match exceeded {MATCH_TIMEOUT_S}s",
        )
    except HTTPException:
        raise
    except (pl.exceptions.ColumnNotFoundError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"match failed: {exc}")
