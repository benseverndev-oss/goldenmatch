from __future__ import annotations

import shutil

import yaml
from fastapi import APIRouter, HTTPException, Request

from goldenmatch.config.schemas import RulesPayload
from goldenmatch.web.rules import load_rules_from_yaml

router = APIRouter(prefix="/api/v1/rules")


def _seed_rules(state) -> dict:
    """Seed in-memory rules from yaml, normalizing the default threshold."""
    seeded = load_rules_from_yaml(state.config_path)
    return {
        "threshold": 0.85 if seeded["threshold"] is None else float(seeded["threshold"]),
        "matchkeys": seeded["matchkeys"],
    }


@router.get("")
def get_rules(request: Request) -> dict:
    state = request.app.state.app_state
    if state.rules is None:
        state.rules = _seed_rules(state)
    return state.rules


@router.put("")
def put_rules(payload: RulesPayload, request: Request) -> dict:
    state = request.app.state.app_state
    state.rules = payload.model_dump()
    return state.rules


@router.post("/save")
def save_rules(request: Request) -> dict:
    state = request.app.state.app_state
    if state.rules is None:
        raise HTTPException(status_code=400, detail="no rules in memory; PUT first")
    if state.config_path is None:
        state.config_path = state.project_root / "goldenmatch.yml"

    if state.config_path.exists():
        shutil.copy2(state.config_path, state.config_path.with_suffix(".yml.bak"))

    existing = {}
    if state.config_path.exists():
        existing = yaml.safe_load(state.config_path.read_text(encoding="utf-8")) or {}
    existing["threshold"] = state.rules["threshold"]
    existing["matchkey"] = state.rules["matchkeys"]
    state.config_path.write_text(yaml.safe_dump(existing, sort_keys=False), encoding="utf-8")
    return {"saved": True, "path": str(state.config_path)}
