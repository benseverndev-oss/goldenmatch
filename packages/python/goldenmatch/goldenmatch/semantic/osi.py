"""Open Semantic Interchange / Apache Ossie reader + emitter (wedge C).

GoldenMatch as an OSI-native identity provider — the bidirectional close of the
semantic-layer arc:

- **consume** an OSI model to learn which keys the metrics join on (`parse_osi_models`,
  `osi_join_keys`) and certify exactly those keys (`certify_osi_relationships`,
  bridging wedge A) — "resolve the identity the metrics actually depend on";
- **emit** valid OSI declaring the GoldenMatch-resolved key as the conformed join
  (`emit_osi_from_crosswalk`, bridging wedge B), with certification/provenance in
  `custom_extensions`.

Targets the Ossie core objects (datasets / fields / relationships / metrics) as of
spec `0.2.0.dev0` (JSON Schema `core-spec/osi-schema.json`). **Schema-faithful:**
the top level is `version` + a `semantic_model` LIST; identity is datasets +
`primary_key`/`unique_keys` + relationships (there is no `entity` object); a
relationship's direction IS its cardinality (`from` = many, `to` = one), so we
never invent `cardinality`, `foreign_key`, or `aggregation` keys — FKs live in the
relationship, aggregation lives inside the metric's SQL expression. GoldenMatch
metadata rides in `custom_extensions` to stay valid against an incubating 0.x spec.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from goldenmatch.semantic.metricflow import _load

OSI_VERSION = "0.2.0.dev0"
DEFAULT_DIALECT = "ANSI_SQL"

# The Ossie 0.2.0.dev0 enums (core-spec/osi-schema.json). Used by validate_osi.
_OSI_DIALECTS = frozenset(
    {"ANSI_SQL", "SNOWFLAKE", "MDX", "TABLEAU", "DATABRICKS", "MAQL", "BIGQUERY"}
)
_OSI_DATATYPES = frozenset({
    "String", "Integer", "Decimal", "Float", "Boolean",
    "Date", "Time", "DateTime", "DateTimeTz", "Opaque",
})


# --- model -------------------------------------------------------------------


@dataclass
class OsiField:
    name: str
    expression: str                      # the (ANSI_SQL) expression — usually the column
    datatype: str | None = None
    is_time: bool = False
    label: str | None = None
    description: str | None = None


@dataclass
class OsiDataset:
    name: str
    source: str | None = None            # physical table ref
    primary_key: list[str] = field(default_factory=list)
    unique_keys: list[list[str]] = field(default_factory=list)
    fields: list[OsiField] = field(default_factory=list)


@dataclass
class OsiRelationship:
    name: str
    from_dataset: str                    # MANY side (`from`)
    to_dataset: str                      # ONE side (`to`)
    from_columns: list[str] = field(default_factory=list)   # FK columns
    to_columns: list[str] = field(default_factory=list)     # referenced PK/unique columns


@dataclass
class OsiMetric:
    name: str
    expression: str                      # aggregation SQL, dataset-qualified columns
    datatype: str | None = None
    description: str | None = None


@dataclass
class OsiModel:
    name: str
    datasets: list[OsiDataset] = field(default_factory=list)
    relationships: list[OsiRelationship] = field(default_factory=list)
    metrics: list[OsiMetric] = field(default_factory=list)
    description: str | None = None
    version: str = OSI_VERSION
    custom_extensions: dict[str, Any] | None = None


# --- parse (consume) ---------------------------------------------------------


def _read_expression(expr: Any) -> str:
    """A field/metric `expression` is `{dialects: [{dialect, expression}]}` (or a
    bare string in loose docs). Prefer ANSI_SQL, else the first dialect."""
    if isinstance(expr, str):
        return expr
    if isinstance(expr, dict):
        dialects = expr.get("dialects") or []
        for d in dialects:
            if isinstance(d, dict) and d.get("dialect") == DEFAULT_DIALECT:
                return str(d.get("expression", ""))
        if dialects and isinstance(dialects[0], dict):
            return str(dialects[0].get("expression", ""))
    return ""


def _parse_dataset(d: dict[str, Any]) -> OsiDataset:
    fields = []
    for f in d.get("fields") or []:
        if not isinstance(f, dict):
            continue
        dim = f.get("dimension") or {}
        fields.append(OsiField(
            name=str(f.get("name", "")),
            expression=_read_expression(f.get("expression")),
            datatype=f.get("datatype"),
            is_time=bool(dim.get("is_time", False)) if isinstance(dim, dict) else False,
            label=f.get("label"),
            description=f.get("description"),
        ))
    pk = d.get("primary_key") or []
    if isinstance(pk, str):
        pk = [pk]
    return OsiDataset(
        name=str(d.get("name", "")),
        source=d.get("source"),
        primary_key=list(pk),
        unique_keys=[list(k) for k in (d.get("unique_keys") or [])],
        fields=fields,
    )


def _parse_relationship(r: dict[str, Any]) -> OsiRelationship:
    def _cols(v: Any) -> list[str]:
        if v is None:
            return []
        return [v] if isinstance(v, str) else list(v)

    return OsiRelationship(
        name=str(r.get("name", "")),
        from_dataset=str(r.get("from", "")),
        to_dataset=str(r.get("to", "")),
        from_columns=_cols(r.get("from_columns")),
        to_columns=_cols(r.get("to_columns")),
    )


def parse_osi_models(source: str | Any) -> list[OsiModel]:
    """Parse an OSI/Ossie document (path, YAML string, or dict) into `OsiModel`s.

    `semantic_model` is a list, so this returns one `OsiModel` per model.
    """
    data = _load(source)
    version = str(data.get("version", OSI_VERSION))
    out: list[OsiModel] = []
    for sm in data.get("semantic_model") or []:
        if not isinstance(sm, dict):
            continue
        out.append(OsiModel(
            name=str(sm.get("name", "")),
            datasets=[_parse_dataset(d) for d in (sm.get("datasets") or []) if isinstance(d, dict)],
            relationships=[_parse_relationship(r) for r in (sm.get("relationships") or []) if isinstance(r, dict)],
            metrics=[
                OsiMetric(name=str(m.get("name", "")), expression=_read_expression(m.get("expression")),
                          datatype=m.get("datatype"), description=m.get("description"))
                for m in (sm.get("metrics") or []) if isinstance(m, dict)
            ],
            description=sm.get("description"),
            version=version,
            custom_extensions=sm.get("custom_extensions"),
        ))
    return out


def osi_join_keys(model: OsiModel) -> list[dict[str, Any]]:
    """The keys the model's relationships join on — i.e. the identity a metric
    depends on. One entry per relationship end: `{dataset, columns, side}`. This
    is what GoldenMatch should resolve/certify (metric-aware)."""
    out: list[dict[str, Any]] = []
    for r in model.relationships:
        out.append({"dataset": r.to_dataset, "columns": r.to_columns, "side": "one", "relationship": r.name})
        out.append({"dataset": r.from_dataset, "columns": r.from_columns, "side": "many", "relationship": r.name})
    return out


# --- emit (produce) ----------------------------------------------------------


def _dialect_expression(expression: str, dialect: str = DEFAULT_DIALECT) -> dict[str, Any]:
    return {"dialects": [{"dialect": dialect, "expression": expression}]}


def emit_osi_field(f: OsiField) -> dict[str, Any]:
    out: dict[str, Any] = {"name": f.name, "expression": _dialect_expression(f.expression or f.name)}
    if f.datatype:
        out["datatype"] = f.datatype
    out["dimension"] = {"is_time": f.is_time}
    if f.label:
        out["label"] = f.label
    if f.description:
        out["description"] = f.description
    return out


def emit_osi_dataset(ds: OsiDataset) -> dict[str, Any]:
    out: dict[str, Any] = {"name": ds.name}
    if ds.source:
        out["source"] = ds.source
    if ds.primary_key:
        out["primary_key"] = list(ds.primary_key)
    if ds.unique_keys:
        out["unique_keys"] = [list(k) for k in ds.unique_keys]
    if ds.fields:
        out["fields"] = [emit_osi_field(f) for f in ds.fields]
    return out


def emit_osi_relationship(r: OsiRelationship) -> dict[str, Any]:
    # NOTE: no `cardinality` key — direction encodes it (from=many, to=one).
    return {
        "name": r.name,
        "from": r.from_dataset,
        "to": r.to_dataset,
        "from_columns": list(r.from_columns),
        "to_columns": list(r.to_columns),
    }


def emit_osi_model(model: OsiModel) -> dict[str, Any]:
    out: dict[str, Any] = {"name": model.name}
    if model.description:
        out["description"] = model.description
    if model.datasets:
        out["datasets"] = [emit_osi_dataset(d) for d in model.datasets]
    if model.relationships:
        out["relationships"] = [emit_osi_relationship(r) for r in model.relationships]
    if model.metrics:
        out["metrics"] = [
            {"name": m.name, "expression": _dialect_expression(m.expression),
             **({"datatype": m.datatype} if m.datatype else {})}
            for m in model.metrics
        ]
    if model.custom_extensions:
        out["custom_extensions"] = model.custom_extensions
    return out


def emit_osi_yaml(models: OsiModel | list[OsiModel], *, version: str = OSI_VERSION) -> str:
    """Render `OsiModel`(s) as a valid OSI document — top-level `version` +
    `semantic_model` list. Round-trips through `parse_osi_models`."""
    if isinstance(models, OsiModel):
        models = [models]
    doc = {"version": version, "semantic_model": [emit_osi_model(m) for m in models]}
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


def emit_osi_from_crosswalk(
    crosswalk: Any,
    *,
    source_dataset: str,
    source_join_column: str | None = None,
    crosswalk_dataset: str = "crosswalk",
    crosswalk_source: str | None = None,
    resolved_field: str | None = None,
    certificate: Any = None,
    model_name: str | None = None,
) -> str:
    """Emit valid OSI for a `ResolvedCrosswalk` (wedge B): a crosswalk dataset keyed
    on `source_pk`, plus a relationship joining the source dataset (many) to it
    (one) — so metrics group by the conformed `resolved_entity_id`. GoldenMatch
    provenance (+ an optional key-integrity certificate) rides in `custom_extensions`.
    """
    src_pk = getattr(crosswalk, "source_pk_column", "source_pk")
    resolved_field = resolved_field or getattr(crosswalk, "resolved_key", "resolved_entity_id")
    join_col = source_join_column or src_pk

    xw = OsiDataset(
        name=crosswalk_dataset,
        source=crosswalk_source,
        primary_key=[src_pk],
        fields=[
            OsiField("source", "source", datatype="String"),
            OsiField(src_pk, src_pk),
            OsiField(resolved_field, resolved_field, datatype="String"),
        ],
    )
    rel = OsiRelationship(
        name=f"{source_dataset}_to_{crosswalk_dataset}",
        from_dataset=source_dataset,
        to_dataset=crosswalk_dataset,
        from_columns=[join_col],
        to_columns=[src_pk],
    )

    gm: dict[str, Any] = {
        "generated_by": "goldenmatch.semantic",
        "resolved_key": resolved_field,
        "n_records": getattr(crosswalk, "n_records", None),
        "n_entities": getattr(crosswalk, "n_entities", None),
        "reduction_ratio": round(getattr(crosswalk, "reduction_ratio", 0.0), 6),
    }
    if certificate is not None:
        gm["key_integrity"] = {
            "uniqueness_estimate": getattr(certificate, "estimate", None),
            "max_fan_out": getattr(certificate, "max_fan_out", None),
            "undercount_estimate": getattr(certificate, "undercount_estimate", None),
        }

    model = OsiModel(
        name=model_name or f"{source_dataset}_resolved",
        datasets=[xw],
        relationships=[rel],
        custom_extensions={"goldenmatch": gm},
    )
    return emit_osi_yaml(model)


# --- bridge: certify the keys an OSI model joins on (metric-aware, wedge A) ----


def certify_osi_relationships(
    model: OsiModel | str | Any, frames: dict[str, Any], *, resolve: bool = False
) -> list[dict[str, Any]]:
    """For each relationship in an OSI model, certify the ONE-side key it joins on
    (the referenced PK) using wedge A — i.e. certify exactly the identity the
    metrics depend on. `frames` maps dataset name -> table; datasets without a
    supplied frame are skipped. `resolve=True` also measures entity
    fragmentation / undercount via ER (fail-open).

    Returns `[{relationship, dataset, key, certificate}]`.
    """
    from goldenmatch.semantic.key_integrity import certify_key_integrity

    if not isinstance(model, OsiModel):
        models = parse_osi_models(model)
        if not models:
            return []
        model = models[0]

    out: list[dict[str, Any]] = []
    for rel in model.relationships:
        df = frames.get(rel.to_dataset)
        if df is None or not rel.to_columns:
            continue
        cert = certify_key_integrity(df, key=rel.to_columns, resolve=resolve)
        out.append({
            "relationship": rel.name,
            "dataset": rel.to_dataset,
            "key": list(rel.to_columns),
            "certificate": cert,
        })
    return out


# --- conformance validation --------------------------------------------------


def _expression_issues(expr: Any, loc: str) -> list[str]:
    """Validate an Ossie `expression` — a str or `{dialects: [{dialect, expression}]}`.
    Returns issues for a missing/malformed expression AND for any dialect name
    outside the Ossie enum (`_OSI_DIALECTS`)."""
    if isinstance(expr, str):
        return []
    if isinstance(expr, dict):
        dialects = expr.get("dialects")
        if not isinstance(dialects, list) or not dialects:
            return [f"{loc}: missing/invalid 'expression'"]
        out: list[str] = []
        for d in dialects:
            dialect = d.get("dialect") if isinstance(d, dict) else None
            if dialect is not None and dialect not in _OSI_DIALECTS:
                out.append(f"{loc}: dialect {dialect!r} not in the Ossie enum")
        return out
    return [f"{loc}: missing/invalid 'expression'"]


def validate_osi(source: str | Any) -> list[str]:
    """Validate an OSI/Ossie document against the spec's structural constraints
    (`core-spec/osi-schema.json`, 0.2.0.dev0) — returns a list of human-readable
    issues, empty when valid. Faithful to the schema's `required` sets + enums,
    and flags the keys the schema does NOT define (cardinality / foreign_key /
    aggregation) so hand-written or third-party docs stay conformant.

    Structural (no `jsonschema` dependency): validates required fields, list shape,
    and enum membership — the checks that keep GoldenMatch-emitted OSI round-trippable
    and portable across Ossie consumers. `validate_osi(emit_osi_yaml(...)) == []`.
    """
    data = _load(source)
    issues: list[str] = []

    if "version" not in data:
        issues.append("missing top-level 'version'")
    sm = data.get("semantic_model")
    if not isinstance(sm, list):
        issues.append("'semantic_model' must be a list of models")
        return issues

    for i, m in enumerate(sm):
        loc = f"semantic_model[{i}]"
        if not isinstance(m, dict):
            issues.append(f"{loc}: must be a mapping")
            continue
        if not str(m.get("name", "")).strip():
            issues.append(f"{loc}: missing 'name'")

        for j, d in enumerate(m.get("datasets") or []):
            dloc = f"{loc}.datasets[{j}]"
            if not isinstance(d, dict) or not str(d.get("name", "")).strip():
                issues.append(f"{dloc}: missing 'name'")
                continue
            if "foreign_key" in d:
                issues.append(f"{dloc}: 'foreign_key' is not an Ossie key — declare FKs in relationships")
            for k, f in enumerate(d.get("fields") or []):
                floc = f"{dloc}.fields[{k}]"
                if not isinstance(f, dict) or not str(f.get("name", "")).strip():
                    issues.append(f"{floc}: missing 'name'")
                    continue
                issues.extend(_expression_issues(f.get("expression"), floc))
                dt = f.get("datatype")
                if dt is not None and dt not in _OSI_DATATYPES:
                    issues.append(f"{floc}: datatype {dt!r} not in the Ossie enum")

        for j, r in enumerate(m.get("relationships") or []):
            rloc = f"{loc}.relationships[{j}]"
            if not isinstance(r, dict):
                issues.append(f"{rloc}: must be a mapping")
                continue
            for req in ("name", "from", "to", "from_columns", "to_columns"):
                if req not in r or r.get(req) in (None, "", []):
                    issues.append(f"{rloc}: missing required '{req}'")
            if "cardinality" in r:
                issues.append(f"{rloc}: 'cardinality' is not an Ossie key — direction is from(many)->to(one)")

        for j, mt in enumerate(m.get("metrics") or []):
            mloc = f"{loc}.metrics[{j}]"
            if not isinstance(mt, dict) or not str(mt.get("name", "")).strip():
                issues.append(f"{mloc}: missing 'name'")
                continue
            issues.extend(_expression_issues(mt.get("expression"), mloc))
            if "aggregation" in mt or "agg" in mt:
                issues.append(f"{mloc}: aggregation belongs inside the metric SQL expression, not a key")

    return issues
