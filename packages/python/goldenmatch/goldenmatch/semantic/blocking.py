"""Metric-aware attribute selection for the semantic-layer resolution tier.

A semantic model already declares which columns are entity **keys**, which are
**measures** (numeric aggregation targets — NOT identity evidence), and which are
**dimensions** (the identity-bearing attributes a metric groups by). GoldenMatch's
resolution tier (`certify_key_integrity(resolve=True)`) runs entity resolution to
detect key fragmentation; feeding that ER the model's own measure/dimension
metadata — instead of blindly profiling every column — is the differentiated
wedge. A measure like `revenue` must never be a blocking key or a match signal,
and a declared dimension like `email` is exactly the attribute to resolve on. No
pure-ER tool has this metadata; the semantic model does.

`semantic_field_roles(source)` reads the declared roles from any of the three
dialects (dbt/MetricFlow, Cube, OSI/Ossie). `metric_aware_attributes(roles,
columns)` turns them into the ER attribute allow-list: the declared dimensions
present in the frame (measures and keys always excluded), with a safe fallback to
"every non-key, non-measure column" when a model declares no dimensions — so a
model that declares dimensions gets the metric-aware selection and one that
doesn't is byte-identical to the blind selection the resolution tier used before.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SemanticFieldRoles:
    """The column roles a semantic model declares, unioned across all of its
    models / cubes / datasets.

    * `keys` — declared entity key columns (primary / natural / unique).
    * `dimensions` — declared dimension columns (identity-bearing attributes).
    * `measures` — declared measure columns (aggregation targets, never identity).
    """

    keys: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    measures: list[str] = field(default_factory=list)


def _dedup(seq: Sequence[str]) -> list[str]:
    """Order-preserving de-duplication (a column may be declared more than once)."""
    seen: set[str] = set()
    out: list[str] = []
    for c in seq:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _frame_columns(df: Any) -> list[str]:
    """Column names of a supported table type, without a full Arrow conversion
    where the type already exposes them."""
    cols = getattr(df, "column_names", None)  # pyarrow.Table
    if cols is not None:
        return list(cols)
    cols = getattr(df, "columns", None)  # polars / pandas DataFrame
    if cols is not None:
        return list(cols)
    if isinstance(df, dict):
        return list(df.keys())
    # Last resort: coerce via the key_integrity Arrow adapter.
    from goldenmatch.semantic.key_integrity import _to_arrow

    return list(_to_arrow(df).column_names)


def semantic_field_roles(source: str | Any) -> SemanticFieldRoles:
    """Read the declared `{keys, dimensions, measures}` roles from a semantic model.

    Args:
        source: a path, raw YAML string, or loaded dict for a dbt/MetricFlow,
            Cube, or OSI/Ossie semantic model (the dialect is auto-detected).

    Returns:
        A `SemanticFieldRoles` with the roles unioned across every model / cube /
        dataset the document declares.
    """
    # Imported lazily to keep this module free of import cycles (the dialect
    # readers and the front-door detector don't import blocking).
    from goldenmatch.semantic.certify import detect_dialect
    from goldenmatch.semantic.metricflow import _load

    data = _load(source)
    dialect = detect_dialect(data)
    keys: list[str] = []
    dimensions: list[str] = []
    measures: list[str] = []

    if dialect == "metricflow":
        from goldenmatch.semantic.metricflow import _entity_column, _measure_column

        for sm in data.get("semantic_models") or []:
            if not isinstance(sm, dict):
                continue
            for ent in sm.get("entities") or []:
                if isinstance(ent, dict) and (col := _entity_column(ent)):
                    keys.append(col)
            for dim in sm.get("dimensions") or []:
                if isinstance(dim, dict) and (col := _entity_column(dim)):
                    # dimensions share the entity `expr`-or-`name` column shape
                    dimensions.append(col)
            for m in sm.get("measures") or []:
                if isinstance(m, dict) and (col := _measure_column(m)):
                    measures.append(col)
    elif dialect == "cube":
        from goldenmatch.semantic.cube import parse_cube_models

        for cube in parse_cube_models(data):
            for d in cube.dimensions:
                (keys if d.primary_key else dimensions).append(d.name)
            for m in cube.measures:
                measures.append(m.name)
    elif dialect == "feast":
        from goldenmatch.semantic.feast import parse_feast_models

        repo = parse_feast_models(data)
        for e in repo.entities:
            keys.extend(e.join_keys)
        # A feature value is never identity evidence (don't merge two customers
        # because they share a churn score), so features are measures.
        for fv in repo.feature_views:
            measures.extend(fv.features)
    elif dialect == "malloy":
        from goldenmatch.semantic.malloy import parse_malloy_models

        for src in parse_malloy_models(data).sources:
            keys.extend(src.primary_key)
            dimensions.extend(src.dimensions)
            measures.extend(src.measures)
    elif dialect == "odcs":
        from goldenmatch.semantic.odcs import parse_odcs_contract

        for obj in parse_odcs_contract(data).schema_objects:
            keys.extend(obj.identity_key())
            # numeric properties are aggregation targets (never identity evidence);
            # the descriptive columns are the dimensions to resolve on.
            measures.extend(obj.numeric_measures())
            dimensions.extend(obj.dimensions())
    else:  # osi
        from goldenmatch.semantic.osi import parse_osi_models

        for model in parse_osi_models(data):
            for ds in model.datasets:
                keys.extend(ds.primary_key)
                key_set = set(ds.primary_key)
                for f in ds.fields:
                    if f.name not in key_set:
                        dimensions.append(f.name)
            # OSI metrics are aggregation expressions (often derived names, not raw
            # frame columns); record them so an incidental same-named column is
            # still excluded from identity evidence.
            for metric in model.metrics:
                if metric.name:
                    measures.append(metric.name)

    return SemanticFieldRoles(
        keys=_dedup(keys),
        dimensions=_dedup(dimensions),
        measures=_dedup(measures),
    )


def metric_aware_attributes(
    roles: SemanticFieldRoles, columns: Sequence[str]
) -> list[str]:
    """The entity-resolution attribute allow-list for a frame, given declared roles.

    Measures and keys are always excluded (a measure is an aggregation target, a
    key is what resolution is checking — neither is identity evidence). When the
    model declares dimensions, the result is exactly the declared dimensions that
    are present in the frame; otherwise it falls back to every remaining column,
    so a model that declares no dimensions is byte-identical to the blind
    selection. The result preserves frame-column order for determinism.

    Args:
        roles: the declared roles (from `semantic_field_roles`).
        columns: the frame's column names.

    Returns:
        The attribute columns to run ER on (never a key or a measure).
    """
    columns = list(columns)
    excluded = set(roles.keys) | set(roles.measures)
    declared_dims = [c for c in roles.dimensions if c in columns and c not in excluded]
    if declared_dims:
        keep = set(declared_dims)
        return [c for c in columns if c in keep]
    # No declared dimensions present → blind fallback (measures/keys still excluded).
    return [c for c in columns if c not in excluded]
