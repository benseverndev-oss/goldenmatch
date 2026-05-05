from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter, Request

from goldenmatch.web.runs import discover_runs, load_run_manifest

router = APIRouter(prefix="/api/v1")


def _load_rules(config_path: Path | None) -> dict:
    if config_path is None or not config_path.exists():
        return {"threshold": None, "matchkeys": []}
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return {
        "threshold": raw.get("threshold"),
        "matchkeys": raw.get("matchkey") or raw.get("matchkeys") or [],
    }


@router.get("/project")
def get_project(request: Request) -> dict:
    state = request.app.state.app_state
    runs = [load_run_manifest(r) for r in discover_runs(state.runs_dir or state.project_root)]
    return {
        "project_root": str(state.project_root),
        "config_path": str(state.config_path) if state.config_path else None,
        "rules": _load_rules(state.config_path),
        "runs": [r.__dict__ for r in runs],
    }
