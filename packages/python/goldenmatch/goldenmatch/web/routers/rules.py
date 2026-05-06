from __future__ import annotations

import os
import shutil

import yaml
from fastapi import APIRouter, HTTPException, Request

from goldenmatch.config.schemas import RulesPayload
from goldenmatch.web.rules import load_rules_from_yaml

router = APIRouter(prefix="/api/v1/rules")

# Single-tenant by design: no concurrency guard on `state.rules` or the YAML
# file. Localhost dev tool — concurrent PUT/save is a non-goal for v1.


def _seeded_rules(state) -> RulesPayload:
    return RulesPayload(**load_rules_from_yaml(state.config_path))


@router.get("")
def get_rules(request: Request) -> dict:
    state = request.app.state.app_state
    if state.rules is None:
        state.rules = _seeded_rules(state)
    return state.rules.model_dump()


@router.put("")
def put_rules(payload: RulesPayload, request: Request) -> dict:
    state = request.app.state.app_state
    state.rules = payload
    return state.rules.model_dump()


@router.post("/save")
def save_rules(request: Request) -> dict:
    state = request.app.state.app_state
    if state.rules is None:
        raise HTTPException(status_code=400, detail="no rules in memory; PUT first")
    if state.config_path is None:
        state.config_path = state.project_root / "goldenmatch.yml"

    existing: dict = {}
    if state.config_path.exists():
        # Snapshot prior on-disk state into .yml.bak before clobbering.
        shutil.copy2(state.config_path, state.config_path.with_suffix(".yml.bak"))
        existing = yaml.safe_load(state.config_path.read_text(encoding="utf-8")) or {}

    # Drop both spellings before writing the canonical singular key, so a file
    # that previously held `matchkeys:` (plural) doesn't end up with both keys.
    existing.pop("matchkey", None)
    existing.pop("matchkeys", None)
    existing["threshold"] = state.rules.threshold
    existing["matchkey"] = [m.model_dump(exclude_none=True) for m in state.rules.matchkeys]

    # Standardization: write the explicit `{rules: {col: [...]}}` shape so
    # the engine's loader accepts it without relying on the shorthand
    # normalizer. None / empty drops the block entirely rather than writing
    # `standardization: null`, which the loader treats as a no-op anyway but
    # leaves a confusing key in the file.
    existing.pop("standardization", None)
    if state.rules.standardization:
        existing["standardization"] = {"rules": dict(state.rules.standardization)}

    # Blocking: serialize the user's BlockingConfig with defaults stripped so
    # the YAML stays compact. Absent / cleared blocking removes the key
    # entirely, returning to "engine picks" behavior.
    existing.pop("blocking", None)
    if state.rules.blocking is not None:
        existing["blocking"] = state.rules.blocking.model_dump(
            exclude_defaults=True, exclude_none=True,
        )

    # Atomic write: tmp file + os.replace so a mid-write failure leaves the
    # original file intact (the .bak still mirrors prior state).
    tmp = state.config_path.with_suffix(".yml.tmp")
    tmp.write_text(yaml.safe_dump(existing, sort_keys=False), encoding="utf-8")
    os.replace(tmp, state.config_path)
    return {"saved": True, "path": str(state.config_path)}
