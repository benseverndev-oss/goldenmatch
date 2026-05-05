from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Request

from goldenmatch.web.rules import load_rules_from_yaml
from goldenmatch.web.runs import discover_runs, load_run_manifest

router = APIRouter(prefix="/api/v1")


@router.get("/project")
def get_project(request: Request) -> dict:
    state = request.app.state.app_state
    runs = [load_run_manifest(r) for r in discover_runs(state.runs_dir or state.project_root)]
    return {
        "project_root": str(state.project_root),
        "config_path": str(state.config_path) if state.config_path else None,
        "rules": load_rules_from_yaml(state.config_path),
        "runs": [asdict(r) for r in runs],
    }
