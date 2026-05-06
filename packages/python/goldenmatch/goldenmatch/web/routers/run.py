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
from datetime import datetime, timezone
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
        config.llm_scorer = LLMScorerConfig(enabled=True)

    if auto_config:
        # Zero-config: run_dedupe_df calls auto_configure_df internally when
        # the config is empty + auto_config=True.
        if config is None:
            config = GoldenMatchConfig()
        result = run_dedupe_df(df, config, output_clusters=True, auto_config=True)
    else:
        result = run_dedupe_df(df, config, output_clusters=True)

    clusters: dict[int, dict] = result.get("clusters") or {}
    scored_pairs: list[tuple[int, int, float]] = []
    for cinfo in clusters.values():
        for (a, b), score in cinfo.get("pair_scores", {}).items():
            scored_pairs.append((int(a), int(b), float(score)))

    enriched = df.with_columns(pl.int_range(0, df.height, dtype=pl.Int64).alias("__row_id__"))
    matchkeys = (config or GoldenMatchConfig()).get_matchkeys()
    lineage_records = build_lineage(
        scored_pairs=scored_pairs,
        df=enriched,
        matchkeys=matchkeys,
        clusters=clusters,
    )

    now = datetime.now(timezone.utc)
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
    except asyncio.TimeoutError:
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
    return result
