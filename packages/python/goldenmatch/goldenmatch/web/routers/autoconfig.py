"""POST /api/v1/autoconfig — let goldenmatch's profiler suggest a starting RulesPayload.

The full ``GoldenMatchConfig`` produced by ``auto_configure_df`` is richer than
the workbench's RulesPayload (multiple matchkey configs, blocking, golden
rules, etc.) but the workbench only edits the matchkey + threshold portion.
This router collapses the auto-configured GoldenMatchConfig into a single
RulesPayload by picking the first weighted matchkey and flattening its fields.
That's lossy on purpose — the user is meant to inspect, tweak, and Run preview.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import polars as pl
from fastapi import APIRouter, HTTPException, Request

from goldenmatch.config.schemas import (
    MatchkeyConfig,
    MatchkeyField,
    RulesPayload,
)
from goldenmatch.core.autoconfig import auto_configure_df

router = APIRouter(prefix="/api/v1")

AUTOCONFIG_TIMEOUT_S = 60
_executor = ThreadPoolExecutor(max_workers=1)


def _pick_matchkeys(
    matchkeys: list[MatchkeyConfig],
) -> tuple[list[MatchkeyField], float]:
    """Collapse a multi-matchkey config into a flat field list + threshold.

    Preference order:
      1. First ``weighted`` matchkey (carries per-field weights + threshold).
      2. First ``probabilistic`` matchkey (use auto-derived link_threshold).
      3. First matchkey of any type, flattened.

    Returns ``([], 0.85)`` if no matchkeys made it through preflight.
    """
    weighted = [m for m in matchkeys if m.type == "weighted"]
    probabilistic = [m for m in matchkeys if m.type == "probabilistic"]
    chosen: MatchkeyConfig | None = None
    if weighted:
        chosen = weighted[0]
    elif probabilistic:
        chosen = probabilistic[0]
    elif matchkeys:
        chosen = matchkeys[0]
    if chosen is None:
        return [], 0.85
    threshold = chosen.threshold or chosen.link_threshold or 0.85
    return list(chosen.fields), float(threshold)


def _ui_safe_field(f: MatchkeyField) -> dict[str, Any]:
    """Project a MatchkeyField into the workbench's expected shape.

    The workbench RuleEditor expects ``{column, scorer, weight, transforms}``
    and renders columns as text inputs, so resolve ``field`` → ``column`` and
    drop any record-level fields (record_embedding's ``columns`` plural form).
    """
    column = f.column or f.field or ""
    return {
        "column": column,
        "scorer": f.scorer or "exact",
        "weight": float(f.weight) if f.weight is not None else 1.0,
        "transforms": list(f.transforms or []),
    }


def _autoconfigure(project_root: Path) -> RulesPayload:
    src = project_root / "data.csv"
    if not src.exists():
        raise FileNotFoundError("source CSV (data.csv) not found in project root")
    df = pl.read_csv(src)

    # `allow_remote_assets=False` keeps the autoconfig offline-safe — preflight
    # demotes embedding scorers to fuzzy alternatives. The workbench's UI also
    # rejects embedding scorers in /preview, so this matches.
    cfg = auto_configure_df(df, allow_remote_assets=False)
    fields, threshold = _pick_matchkeys(cfg.get_matchkeys())

    # The workbench rejects matchkeys whose scorer is in {embedding, record_embedding}
    # at preview time — strip them upfront so the user doesn't immediately hit a 400.
    UNSUPPORTED = {"embedding", "record_embedding"}
    safe_fields = [f for f in fields if (f.scorer or "exact") not in UNSUPPORTED]
    if not safe_fields:
        # Fall back: keep one field with a fuzzy scorer so the editor isn't empty.
        if fields:
            f = fields[0]
            safe_fields = [
                MatchkeyField(
                    field=f.field,
                    column=f.column,
                    scorer="jaro_winkler",
                    weight=f.weight if f.weight is not None else 1.0,
                    transforms=list(f.transforms or []),
                )
            ]

    return RulesPayload(
        threshold=max(0.0, min(1.0, threshold)),
        matchkeys=[MatchkeyField(**_ui_safe_field(f)) for f in safe_fields],
    )


@router.post("/autoconfig")
async def autoconfigure(request: Request) -> dict:
    state = request.app.state.app_state
    loop = asyncio.get_running_loop()
    try:
        payload = await asyncio.wait_for(
            loop.run_in_executor(_executor, lambda: _autoconfigure(state.project_root)),
            timeout=AUTOCONFIG_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=408,
            detail=f"autoconfigure exceeded {AUTOCONFIG_TIMEOUT_S}s on this dataset",
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # autoconfig has many narrow failure modes
        raise HTTPException(
            status_code=400, detail=f"autoconfigure failed: {exc}"
        )

    # Adopt the suggestion as in-memory rules so the user can preview / save.
    state.rules = payload
    return payload.model_dump()
