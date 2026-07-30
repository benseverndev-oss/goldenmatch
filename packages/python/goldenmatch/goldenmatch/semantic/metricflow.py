"""Read declared entity keys + measures from a dbt / MetricFlow semantic model.

MetricFlow declares, per `semantic_models[]`: `entities` (with `type` one of
primary/foreign/unique/natural), `measures`, and a default `agg_time_dimension`.
Joins across semantic models happen implicitly on *shared entities* — so the
primary entity's key is exactly what `certify_key_integrity` needs to check.

`parse_semantic_models(source) -> list[DeclaredKeySpec]` extracts that surface so
you can point the certifier at a real dbt project instead of hand-passing a key.
This reads the *declaration* only; it does not touch a warehouse.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_PRIMARY_ENTITY_TYPES = {"primary", "natural"}


@dataclass
class DeclaredKeySpec:
    """One semantic model's declared identity, ready to feed `certify_key_integrity`."""

    model: str
    key: list[str]                       # primary/natural entity column(s)
    measures: list[str] = field(default_factory=list)
    grain: list[str] | None = None       # default agg_time_dimension, if any
    foreign_keys: list[str] = field(default_factory=list)  # foreign entities (join edges)


def _entity_column(entity: dict[str, Any]) -> str:
    """The physical column an entity maps to: `expr` if given, else `name`."""
    expr = entity.get("expr")
    if isinstance(expr, str) and expr.strip():
        return expr.strip()
    return str(entity.get("name", "")).strip()


def _measure_column(measure: dict[str, Any]) -> str:
    expr = measure.get("expr")
    if isinstance(expr, str) and expr.strip():
        return expr.strip()
    return str(measure.get("name", "")).strip()


def _load(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    if isinstance(source, dict):
        return source
    if isinstance(source, (str, Path)) and os.path.exists(source):
        with open(source, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    # treat as raw YAML text
    return yaml.safe_load(str(source)) or {}


def parse_semantic_models(source: str | Path | dict[str, Any]) -> list[DeclaredKeySpec]:
    """Parse dbt/MetricFlow `semantic_models` YAML into `DeclaredKeySpec`s.

    Args:
        source: a path to a YAML file, a raw YAML string, or an already-loaded dict.

    Returns:
        One `DeclaredKeySpec` per semantic model that declares a primary/natural
        entity (models without one are skipped — nothing to certify).
    """
    data = _load(source)
    models = data.get("semantic_models") or []
    specs: list[DeclaredKeySpec] = []

    for sm in models:
        if not isinstance(sm, dict):
            continue
        name = str(sm.get("name", "")).strip()
        entities = sm.get("entities") or []

        primary_key: list[str] = []
        foreign_keys: list[str] = []
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            etype = str(ent.get("type", "")).strip().lower()
            col = _entity_column(ent)
            if not col:
                continue
            if etype in _PRIMARY_ENTITY_TYPES:
                primary_key.append(col)
            elif etype == "foreign":
                foreign_keys.append(col)
            elif etype == "unique":
                # a unique (non-primary) key is still a valid identity to check;
                # only promote it when no primary/natural entity was declared.
                foreign_keys.append(col)

        if not primary_key:
            # No primary/natural entity → nothing to certify for this model.
            continue

        measures = [
            c for m in (sm.get("measures") or [])
            if isinstance(m, dict) and (c := _measure_column(m))
        ]

        grain: list[str] | None = None
        agg_time = (sm.get("defaults") or {}).get("agg_time_dimension")
        if isinstance(agg_time, str) and agg_time.strip():
            grain = [agg_time.strip()]

        specs.append(
            DeclaredKeySpec(
                model=name,
                key=primary_key,
                measures=measures,
                grain=grain,
                foreign_keys=foreign_keys,
            )
        )

    return specs
