"""Read / emit dbt / MetricFlow semantic-model entity declarations.

MetricFlow declares, per `semantic_models[]`: `entities` (with `type` one of
primary/foreign/unique/natural), `measures`, and a default `agg_time_dimension`.
Joins across semantic models happen implicitly on *shared entities* — so the
primary entity's key is exactly what `certify_key_integrity` needs to check.

- `parse_semantic_models(source) -> list[DeclaredKeySpec]` (wedge A) reads the
  declared key so you can point the certifier at a real dbt project.
- `emit_semantic_model` / `emit_metricflow_yaml` (wedge B) go the other way: given
  a GoldenMatch-resolved key, generate the `semantic_models` YAML that declares
  that conformed key as the PRIMARY entity — so every metric joins on resolved
  identity. `parse_semantic_models(emit_metricflow_yaml(...))` round-trips.

This reads/writes the *declaration* only; it does not touch a warehouse.
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


# --- emit (wedge B): resolved key -> MetricFlow semantic-model declaration ------


def emit_semantic_model(
    model: str,
    *,
    resolved_key: str,
    entity_name: str | None = None,
    source_key: str | None = None,
    measures: list[str] | tuple[str, ...] = (),
    grain: list[str] | str | None = None,
    model_ref: str | None = None,
    measure_agg: str = "sum",
) -> dict[str, Any]:
    """Build ONE MetricFlow `semantic_models[]` entry declaring `resolved_key` as
    the PRIMARY entity — the GoldenMatch-conformed join key every metric inherits.

    The original per-source key (`source_key`), if given, is declared as a
    `unique` entity so it stays queryable but is no longer the join primary.

    Args:
        model: the semantic model / dbt model name.
        resolved_key: the resolved-entity column (e.g. `resolved_entity_id`) to
            declare as the primary entity's `expr`.
        entity_name: the entity's logical name (defaults to `model`).
        source_key: the original source primary key, declared `unique` if given.
        measures: measure column names (emitted with `agg=measure_agg`, `expr=name`).
        grain: the default agg time dimension (str or 1-list), if any.
        model_ref: the `model:` ref expression (defaults to `ref('<model>')`).
        measure_agg: the aggregation stamped on each measure (a scaffold default —
            set the real aggregation per measure in your project).
    """
    entities: list[dict[str, Any]] = [
        {"name": entity_name or model, "type": "primary", "expr": resolved_key},
    ]
    if source_key and source_key != resolved_key:
        entities.append({"name": source_key, "type": "unique", "expr": source_key})

    sm: dict[str, Any] = {
        "name": model,
        "model": model_ref or f"ref('{model}')",
        "entities": entities,
    }

    grain_list = [grain] if isinstance(grain, str) else list(grain or [])
    if grain_list:
        sm["defaults"] = {"agg_time_dimension": grain_list[0]}

    if measures:
        sm["measures"] = [
            {"name": m, "agg": measure_agg, "expr": m} for m in measures
        ]
    return sm


def emit_metricflow_yaml(models: dict[str, Any] | list[dict[str, Any]]) -> str:
    """Render one or more `emit_semantic_model` dicts as a `semantic_models` YAML
    document (block style, key order preserved). Round-trips through
    `parse_semantic_models`."""
    if isinstance(models, dict):
        models = [models]
    payload = {"semantic_models": list(models)}
    return yaml.safe_dump(payload, sort_keys=False, default_flow_style=False)


def emit_from_crosswalk(
    crosswalk: Any,
    model: str,
    *,
    entity_name: str | None = None,
    measures: list[str] | tuple[str, ...] = (),
    grain: list[str] | str | None = None,
    model_ref: str | None = None,
) -> str:
    """Emit the `semantic_models` YAML for a `ResolvedCrosswalk`: its
    `resolved_key` becomes the primary entity, its `source_pk_column` the `unique`
    source key. Convenience over `emit_semantic_model` + `emit_metricflow_yaml`."""
    sm = emit_semantic_model(
        model,
        resolved_key=getattr(crosswalk, "resolved_key", "resolved_entity_id"),
        entity_name=entity_name,
        source_key=getattr(crosswalk, "source_pk_column", None),
        measures=measures,
        grain=grain,
        model_ref=model_ref,
    )
    return emit_metricflow_yaml(sm)
