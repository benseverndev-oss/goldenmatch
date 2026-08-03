"""Dialect emit for discovered models (PR-9).

Turns the discovered structure (`ProposedTable`s + the certified join graph) into a
draft catalog in one of the three supported dialects. MetricFlow was the original
slice; `cube` and `osi` are added here. Each builder reuses the EXISTING dialect
emitters (`emit_metricflow_yaml` / `emit_cube_yaml` / `emit_osi_yaml`) and their
dataclasses — this module only maps discovery types onto them.

Only the **trustworthy** joins are emitted (an untrustworthy FK is a bad join, not a
pre-graded one). The MetricFlow path is byte-identical to the pre-PR-9 inline emit.
"""
from __future__ import annotations

from typing import Any

# discovered dimension `kind` -> Cube dimension type / OSI is_time.
_CUBE_DIM_TYPE = {"categorical": "string", "date": "time", "geo": "geo"}


def build_model_yaml(dialect: str, proposed_tables: list[Any], joins: list[Any]) -> str:
    """Emit the discovered model as YAML in `dialect` (metricflow / cube / osi)."""
    if dialect == "metricflow":
        return _build_metricflow(proposed_tables)
    if dialect == "cube":
        return _build_cube(proposed_tables, joins)
    if dialect == "osi":
        return _build_osi(proposed_tables, joins)
    return ""  # unreachable: dialect is validated by the caller.


def _trustworthy_joins(joins: list[Any]) -> list[Any]:
    return [j for j in joins if getattr(j, "is_trustworthy", False)]


def _hierarchies_dicts(pt: Any) -> list[dict[str, Any]]:
    return [{"levels": list(h.levels), "confidence": h.confidence}
            for h in getattr(pt, "hierarchies", []) or []]


def _gm_meta(pt: Any) -> dict[str, Any]:
    """The `goldenmatch` meta block for a table: the key-integrity verdict + any
    discovered dimension hierarchies. Empty dict when neither is present."""
    from goldenmatch.core.key_integrity_certificate import certificate_verdict

    gm: dict[str, Any] = {}
    if pt.key is not None and pt.key.certificate is not None:
        gm["key_integrity"] = certificate_verdict(pt.key.certificate)
    hier = _hierarchies_dicts(pt)
    if hier:
        gm["hierarchies"] = hier
    return gm


def _build_metricflow(proposed_tables: list[Any]) -> str:
    """The original inline emit, unchanged: entities + sum-safe measures per table."""
    from goldenmatch.semantic import emit_metricflow_yaml, emit_semantic_model

    models = []
    metrics: list[dict[str, Any]] = []
    for pt in proposed_tables:
        if pt.key is None:
            continue
        safe_measures = [m.column for m in pt.measures if m.safe_to_sum]
        sm = emit_semantic_model(
            pt.table,
            resolved_key=pt.key.columns[0],
            entity_name=pt.entity_type or pt.table,
            measures=safe_measures,
            certificate=pt.key.certificate,
        )
        hier = _hierarchies_dicts(pt)
        if hier:  # additive to the meta.goldenmatch block emit_semantic_model set
            sm.setdefault("meta", {}).setdefault("goldenmatch", {})["hierarchies"] = hier
        # Native metrics: declare a COUNT measure so averages have a denominator, then
        # emit each derived metric as a MetricFlow ratio metric.
        if pt.metrics:
            count_name = f"{pt.table}_count"
            sm.setdefault("measures", []).append(
                {"name": count_name, "agg": "count", "expr": pt.key.columns[0]}
            )
            for mt in pt.metrics:
                denom = count_name if mt.kind == "average" else mt.denominator
                metrics.append({
                    "name": mt.name, "type": "ratio",
                    "type_params": {"numerator": mt.numerator, "denominator": denom},
                })
        models.append(sm)
    if not models:
        return ""
    if metrics:
        import yaml as _yaml

        return _yaml.safe_dump(
            {"semantic_models": models, "metrics": metrics},
            sort_keys=False, default_flow_style=False,
        )
    return emit_metricflow_yaml(models)


def _build_cube(proposed_tables: list[Any], joins: list[Any]) -> str:
    """One Cube per table: grain -> primary_key dimensions, discovered dimensions,
    sum-safe measures, the key-integrity verdict in `meta.goldenmatch`, and the
    trustworthy join graph as `many_to_one` CubeJoins on the FROM cube."""
    from goldenmatch.semantic.cube import (
        Cube,
        CubeDimension,
        CubeJoin,
        CubeMeasure,
        emit_cube_yaml,
    )

    joins_by_from: dict[str, list[Any]] = {}
    for j in _trustworthy_joins(joins):
        joins_by_from.setdefault(j.from_table, []).append(j)

    cubes = []
    for pt in proposed_tables:
        if pt.key is None:
            continue
        key_cols = list(pt.key.columns)
        dims = [CubeDimension(c, c, type="string", primary_key=True) for c in key_cols]
        for d in pt.dimensions:
            if d.column not in key_cols:
                dims.append(CubeDimension(d.column, d.column,
                                          type=_CUBE_DIM_TYPE.get(d.kind, "string")))
        measures = [CubeMeasure(m.column, type="sum", sql=m.column)
                    for m in pt.measures if m.safe_to_sum]
        if pt.metrics:  # a count + calculated number measures referencing the sums
            measures.append(CubeMeasure("count", type="count"))
            for mt in pt.metrics:
                denom = "count" if mt.kind == "average" else mt.denominator
                measures.append(CubeMeasure(
                    mt.name, type="number", sql=f"{{{mt.numerator}}} / {{{denom}}}"))
        cube_joins = [
            CubeJoin(
                name=j.to_table,
                relationship=j.relationship or "many_to_one",
                sql=f"{{CUBE}}.{j.from_column} = {{{j.to_table}.{j.to_column}}}",
            )
            for j in joins_by_from.get(pt.table, [])
        ]
        cube = Cube(name=pt.table, sql_table=pt.table, dimensions=dims,
                    measures=measures, joins=cube_joins)
        gm = _gm_meta(pt)
        if gm:
            cube.meta = {"goldenmatch": gm}
        cubes.append(cube)

    return emit_cube_yaml(cubes) if cubes else ""


def _build_osi(proposed_tables: list[Any], joins: list[Any]) -> str:
    """One OsiDataset per table (grain -> primary_key list, discovered fields,
    sum-safe measures -> OsiMetrics), the trustworthy joins as OsiRelationships, and
    the per-table key-integrity verdicts under `custom_extensions.goldenmatch`."""
    from goldenmatch.core.key_integrity_certificate import certificate_verdict
    from goldenmatch.semantic.osi import (
        OsiDataset,
        OsiField,
        OsiMetric,
        OsiModel,
        OsiRelationship,
        emit_osi_yaml,
    )

    datasets = []
    metrics: list[Any] = []
    key_integrity: dict[str, Any] = {}
    hierarchies: dict[str, Any] = {}
    for pt in proposed_tables:
        if pt.key is None:
            continue
        key_cols = list(pt.key.columns)
        fields = [OsiField(c, c) for c in key_cols]
        for d in pt.dimensions:
            if d.column not in key_cols:
                fields.append(OsiField(d.column, d.column, is_time=(d.kind == "date")))
        datasets.append(OsiDataset(name=pt.table, source=pt.table,
                                   primary_key=key_cols, fields=fields))
        for m in pt.measures:
            if m.safe_to_sum:
                metrics.append(OsiMetric(f"{pt.table}_{m.column}_total",
                                         expression=f"SUM({pt.table}.{m.column})"))
        # Derived metrics (averages + ratios) as dataset-qualified OSI metrics.
        grain_key = pt.key.columns[0]
        for mt in pt.metrics:
            if mt.kind == "average":
                expr = f"SUM({pt.table}.{mt.numerator}) / COUNT({pt.table}.{grain_key})"
            else:
                expr = f"SUM({pt.table}.{mt.numerator}) / SUM({pt.table}.{mt.denominator})"
            metrics.append(OsiMetric(mt.name, expression=expr))
        if pt.key.certificate is not None:
            key_integrity[pt.table] = certificate_verdict(pt.key.certificate)
        hier = _hierarchies_dicts(pt)
        if hier:
            hierarchies[pt.table] = hier

    if not datasets:
        return ""

    relationships = [
        OsiRelationship(
            name=f"{j.from_table}_to_{j.to_table}",
            from_dataset=j.from_table,
            to_dataset=j.to_table,
            from_columns=[j.from_column],
            to_columns=[j.to_column],
        )
        for j in _trustworthy_joins(joins)
    ]
    gm_ext: dict[str, Any] = {}
    if key_integrity:
        gm_ext["key_integrity"] = key_integrity
    if hierarchies:
        gm_ext["hierarchies"] = hierarchies
    ext = {"goldenmatch": gm_ext} if gm_ext else None
    model = OsiModel(name="discovered", datasets=datasets,
                     relationships=relationships, metrics=metrics,
                     custom_extensions=ext)
    return emit_osi_yaml(model)
