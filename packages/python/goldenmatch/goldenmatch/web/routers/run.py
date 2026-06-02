"""POST /api/v1/run — execute a real (non-sampled) run, write to disk.

The workbench's preview path runs in-process on a sampled slice and stores
results in a bounded LRU registry — good for iterating on rules, useless for
producing artifacts the rest of the team can read. This route runs the engine
on the full ``data.csv`` and writes ``{run_name}_lineage.json`` +
``{run_name}_clusters.csv`` to the project root, where ``discover_runs``
finds them on the next ``GET /api/v1/project``.

Three modes via the request body:

  - default: use the workbench's current rules (``state.rules``).
  - ``auto_config=true``: skip the user's rules and let
    ``goldenmatch.dedupe_df`` zero-config the run from ``data.csv``.
  - ``llm_boost=true``: enable LLMScorerConfig — borderline pairs (0.75-0.95)
    get an LLM second opinion. Requires OPENAI_API_KEY or ANTHROPIC_API_KEY in
    env. The LLM scorer has graceful degradation so a missing key fails the
    LLM step, not the whole run.

Steward labels written via ``POST /api/v1/labels`` are mirrored into the
Learning Memory store, which the pipeline applies on every run via
``apply_corrections``. So "Save run to project" after labeling really does
incorporate those decisions — the UI promise is honored end-to-end.
"""
from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from goldenmatch.config.schemas import (
    GoldenMatchConfig,
    LLMScorerConfig,
    RulesPayload,
)
from goldenmatch.core.lineage import build_lineage
from goldenmatch.core.pipeline import run_dedupe_df

router = APIRouter(prefix="/api/v1")

RUN_TIMEOUT_S = 600  # 10 minutes — full-data runs on real datasets need air
_executor = ThreadPoolExecutor(max_workers=1)


class RunRequest(BaseModel):
    auto_config: bool = False
    llm_boost: bool = False
    rules: RulesPayload | None = None


def _execute_run(
    *,
    project_root: Path,
    auto_config: bool,
    llm_boost: bool,
    rules: RulesPayload | None,
) -> dict[str, Any]:
    src = project_root / "data.csv"
    if not src.exists():
        raise FileNotFoundError("source CSV (data.csv) not found in project root")

    df = pl.read_csv(src)

    config: GoldenMatchConfig | None = None
    if not auto_config:
        if rules is None:
            raise ValueError("rules are required when auto_config is false")
        # Reuse the same translation as preview so OR-semantics across matchkey
        # rows are preserved (see web/preview.py::_build_config docstring).
        from goldenmatch.web.preview import _build_config
        config = _build_config(rules)

    if llm_boost:
        # Need an API key in env for the LLM scorer to actually call out.
        # Surface a clean 400 if none — graceful-degrade is fine but the
        # user explicitly asked for llm_boost, so silence here is wrong.
        if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
            raise RuntimeError(
                "llm_boost requested but no OPENAI_API_KEY or ANTHROPIC_API_KEY "
                "in environment. Set one or set llm_boost=false."
            )
        if config is None:
            config = GoldenMatchConfig()  # zero-config path will populate inside pipeline
        # Apply the user's persisted cost / call caps. The BudgetTracker enforces
        # both during the run; without these the pipeline defaults would still
        # cap, but at $1 / 5000 calls — usually higher than what a workbench
        # iteration wants.
        from goldenmatch.config.schemas import BudgetConfig
        from goldenmatch.web.settings import load_settings
        s = load_settings()
        config.llm_scorer = LLMScorerConfig(
            enabled=True,
            budget=BudgetConfig(
                max_cost_usd=s.llm_max_cost_usd,
                max_calls=s.llm_max_calls,
            ),
        )

    ctrl_profile: Any = None
    ctrl_history: Any = None
    if auto_config:
        # Zero-config: call auto_configure_df *before* the pipeline so the
        # pipeline never re-invokes auto-config. Eliminates double-pipeline-run
        # when the Task 5.1 controller loop is in play (Task 5.2 fix).
        from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN, auto_configure_df
        config = auto_configure_df(df)
        # Capture controller telemetry from the ContextVar — same mechanism
        # _api.dedupe_df uses to surface PostflightReport.controller_*. The
        # /api/v1/controller/telemetry endpoint reads this off AppState.
        _ctrl_state = _LAST_CONTROLLER_RUN.get()
        if _ctrl_state is not None:
            ctrl_profile, ctrl_history = _ctrl_state
        result = run_dedupe_df(df, config, output_clusters=True, auto_config=False)
    else:
        result = run_dedupe_df(df, config, output_clusters=True)

    clusters: dict[int, dict] = result.get("clusters") or {}
    scored_pairs: list[tuple[int, int, float]] = result.get("scored_pairs") or []

    enriched = df.with_columns(pl.int_range(0, df.height, dtype=pl.Int64).alias("__row_id__"))
    matchkeys = (config or GoldenMatchConfig()).get_matchkeys()
    lineage_records = build_lineage(
        scored_pairs=scored_pairs,
        df=enriched,
        matchkeys=matchkeys,
        clusters=clusters,
    )

    now = datetime.now(UTC)
    run_name = now.strftime("%Y%m%d_%H%M%S")
    lineage = {
        "generated_at": now.isoformat(),
        "run_name": run_name,
        "total_pairs": len(lineage_records),
        "pairs": lineage_records,
    }

    # Build clusters CSV in-memory then write — the writer matches the shape
    # discover_runs expects (header row_id,cluster_id; one row per record).
    csv_lines = ["row_id,cluster_id"]
    rid_to_cid: dict[int, int] = {}
    for cid, cinfo in clusters.items():
        for member in cinfo.get("members", []):
            rid_to_cid[int(member)] = int(cid)
    next_singleton = (max(rid_to_cid.values()) + 1) if rid_to_cid else 1
    for rid in range(df.height):
        cid = rid_to_cid.get(rid)
        if cid is None:
            cid = next_singleton
            next_singleton += 1
        csv_lines.append(f"{rid},{cid}")

    lineage_path = project_root / f"{run_name}_lineage.json"
    clusters_path = project_root / f"{run_name}_clusters.csv"
    lineage_path.write_text(json.dumps(lineage, ensure_ascii=False), encoding="utf-8")
    clusters_path.write_text("\n".join(csv_lines) + "\n", encoding="utf-8")

    return {
        "run_name": run_name,
        "row_count": df.height,
        "cluster_count": len(set(rid_to_cid.values())) if rid_to_cid else df.height,
        "total_pairs": len(lineage_records),
        "lineage_path": str(lineage_path),
        "clusters_path": str(clusters_path),
        "auto_config": auto_config,
        "llm_boost": llm_boost,
        # Internal channels — not part of the public response shape; the
        # router pops these before returning JSON. Kept here so the worker
        # thread can hand telemetry back to the request thread without a
        # second contextvar dance.
        "_ctrl_profile": ctrl_profile,
        "_ctrl_history": ctrl_history,
        "_ctrl_config": config,
    }


@router.post("/run")
async def run_real(payload: RunRequest, request: Request) -> dict:
    state = request.app.state.app_state
    rules = payload.rules or state.rules
    if not payload.auto_config and rules is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "no rules to run with — pass `rules` in the body, set auto_config=true, "
                "or first edit / autoconfig the rules via the workbench."
            ),
        )
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                _executor,
                lambda: _execute_run(
                    project_root=state.project_root,
                    auto_config=payload.auto_config,
                    llm_boost=payload.llm_boost,
                    rules=None if payload.auto_config else rules,
                ),
            ),
            timeout=RUN_TIMEOUT_S,
        )
    except TimeoutError:
        raise HTTPException(
            status_code=408,
            detail=f"run exceeded {RUN_TIMEOUT_S}s — try a smaller dataset or break the work up",
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (pl.exceptions.ColumnNotFoundError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"run failed: {exc}")

    # Pop the internal controller telemetry channels off the result dict and
    # stash on AppState so /api/v1/controller/telemetry can serve them. These
    # are unset for non-auto-config runs (the user supplied rules; the
    # controller never ran), in which case we leave state untouched so a prior
    # auto-config session's telemetry stays visible until the next one.
    _profile = result.pop("_ctrl_profile", None)
    _history = result.pop("_ctrl_history", None)
    _cfg = result.pop("_ctrl_config", None)
    if payload.auto_config:
        state.last_controller_profile = _profile
        state.last_controller_history = _history
        state.last_controller_committed_config = _cfg
        state.last_controller_source = "run"
        state.last_controller_run_name = result.get("run_name")
        state.last_controller_recorded_at = datetime.now(UTC).isoformat()
    return result
