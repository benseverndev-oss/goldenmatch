"""ODCS (Open Data Contract Standard) reader + emitter — the data-contract dialect.

A data contract is the most literal statement of the semantic-layer thesis: it is
a machine-readable *promise* about a dataset's shape — and its identity promise is
declared right there in the schema. In ODCS v3 (Bitol / Linux Foundation), a
`schema` object's properties carry **`primaryKey: true`** (ordered by
**`primaryKeyPosition`** for a composite key) and **`unique: true`** — the contract
asserts "these columns identify a row." Nobody checks that assertion against the
data. That is the same gap the metrics-layer wedge catches, and on a data contract
it is the *headline* term: the three break modes bite the contract's own guarantees:

- **duplicated primary key -> the uniqueness promise is false**: the contract says
  `order_id` is the primary key, but it fans out — every downstream `sum`/`count`
  that trusts the contract double-counts;
- **fragmented identity -> undercount**: one real entity split across key values
  breaks the contract's grain silently;
- **broken `unique` constraint**: a declared-unique `email` that isn't.

ODCS `primaryKey`/`primaryKeyPosition` maps one-to-one onto MetricFlow
`entity (primary)`, Cube `primary_key`, and Malloy `primary_key` — so exactly the
same certifier applies:

- **consume** a contract to learn each schema object's declared identity keys — the
  composite primary key AND each standalone `unique` property (`parse_odcs_contract`,
  `odcs_identity_keys`) — "what to resolve";
- **certify** (bridge to wedge A) each declared key against the data, with the
  object's **numeric properties as the fan-out measures** a duplicated key would
  inflate under aggregation (`certify_odcs_contract`);
- **emit** (bridge to wedge B) an ODCS contract whose schema object declares the
  GoldenMatch-resolved key as `primaryKey`, provenance in `customProperties`
  (`emit_odcs_from_crosswalk`). `parse_odcs_contract(emit_odcs_yaml(...))`
  round-trips.

Reads the DECLARATION only — a declarative ODCS doc (v3 `schema:`/`properties:`,
tolerant of the v2 `dataset:`/`columns:` spelling on read). No `open-data-contract-
standard` dependency; it never touches a warehouse. Library-only, advisory,
parity-free (it plugs into the existing `certify_semantic_model` dialect dispatch,
adding no new surface).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from goldenmatch.semantic.metricflow import _load

# ODCS `logicalType` values that denote a numeric column — the ones a duplicated
# key fans out under aggregation, so they're the metric-aware measures. (ODCS's
# vocabulary is {string, date, number, integer, object, array, boolean}; the
# extra spellings accept a physicalType/loose-doc leaking through.)
_NUMERIC_LOGICAL = {
    "number", "integer", "decimal", "float", "double", "bigint", "long", "numeric",
}


# --- model -------------------------------------------------------------------


@dataclass
class ODCSProperty:
    """One column property of an ODCS schema object. `primary_key` +
    `primary_key_position` declare the (composite) identity key; `unique` is a
    standalone uniqueness constraint. `logical_type` decides whether the column is
    a numeric fan-out measure."""

    name: str
    logical_type: str = ""
    physical_type: str = ""
    primary_key: bool = False
    primary_key_position: int | None = None
    unique: bool = False
    required: bool = False

    @property
    def is_numeric(self) -> bool:
        return self.logical_type.strip().lower() in _NUMERIC_LOGICAL


@dataclass
class ODCSSchemaObject:
    """One ODCS `schema` object (a table / dataset). `properties` carry the
    declared identity. `quality` is passed through opaquely (GoldenMatch does not
    author quality rules — it certifies the identity ones hold)."""

    name: str
    physical_name: str | None = None
    properties: list[ODCSProperty] = field(default_factory=list)
    quality: list[Any] = field(default_factory=list)

    def identity_key(self) -> list[str]:
        """The declared composite primary key: `primaryKey: true` properties in
        `primaryKeyPosition` order (properties without a position sort last, in
        declaration order — a stable tiebreak)."""
        pk = [p for p in self.properties if p.primary_key]
        pk.sort(key=lambda p: (p.primary_key_position is None,
                               p.primary_key_position or 0))
        return [p.name for p in pk]

    def unique_keys(self) -> list[str]:
        """Standalone `unique: true` properties that are NOT already part of the
        primary key — each a separate candidate key the contract promises."""
        pk_set = set(self.identity_key())
        return [p.name for p in self.properties if p.unique and p.name not in pk_set]

    def numeric_measures(self) -> list[str]:
        """Numeric non-key properties — the columns a duplicated key inflates."""
        key_set = set(self.identity_key())
        return [p.name for p in self.properties if p.is_numeric and p.name not in key_set]

    def dimensions(self) -> list[str]:
        """Non-numeric non-key properties — the identity-bearing attributes."""
        key_set = set(self.identity_key())
        return [
            p.name for p in self.properties
            if not p.is_numeric and p.name not in key_set
        ]


@dataclass
class ODCSContract:
    api_version: str = ""
    kind: str = "DataContract"
    id: str = ""
    version: str = ""
    name: str = ""
    status: str = ""
    schema_objects: list[ODCSSchemaObject] = field(default_factory=list)
    custom_properties: list[dict[str, Any]] = field(default_factory=list)

    def object_by_name(self, name: str) -> ODCSSchemaObject | None:
        for o in self.schema_objects:
            if o.name == name:
                return o
        return None


# --- parse (consume) ---------------------------------------------------------


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


def _property_from_dict(p: dict[str, Any]) -> ODCSProperty:
    # v3 uses `name`/`primaryKey`/`unique`; the v2 spelling was
    # `column`/`isPrimary`/`isUnique` — accept both on read.
    name = str(p.get("name", p.get("column", ""))).strip()
    pos = p.get("primaryKeyPosition")
    return ODCSProperty(
        name=name,
        logical_type=str(p.get("logicalType", "")).strip(),
        physical_type=str(p.get("physicalType", "")).strip(),
        primary_key=_as_bool(p.get("primaryKey", p.get("isPrimary", False))),
        primary_key_position=(int(pos) if isinstance(pos, (int, str)) and str(pos).strip()
                              and str(pos).strip().lstrip("-").isdigit() else None),
        unique=_as_bool(p.get("unique", p.get("isUnique", False))),
        required=_as_bool(p.get("required", False)),
    )


def _object_from_dict(o: dict[str, Any]) -> ODCSSchemaObject:
    # v3: `name` + `properties`; v2: `table` + `columns`.
    name = str(o.get("name", o.get("table", ""))).strip()
    props = o.get("properties")
    if not isinstance(props, list):
        props = o.get("columns") or []
    quality = o.get("quality")
    return ODCSSchemaObject(
        name=name,
        physical_name=(str(o["physicalName"]).strip() if o.get("physicalName") else None),
        properties=[_property_from_dict(p) for p in props if isinstance(p, dict)],
        quality=list(quality) if isinstance(quality, list) else [],
    )


def parse_odcs_contract(source: str | Any) -> ODCSContract:
    """Parse a declarative ODCS data contract (path, YAML/JSON string, or dict) into
    an `ODCSContract`.

    Expects the ODCS v3 shape — `kind: DataContract` + a top-level `schema:` list of
    objects, each with `properties`. The older v2 spelling (`dataset:` + `columns:`,
    `isPrimary`/`isUnique`) is accepted on read so an existing contract certifies
    without a rewrite. GoldenMatch never depends on the `open-data-contract-standard`
    package — this reads the plain document.
    """
    data = _load(source)
    if not isinstance(data, dict):
        return ODCSContract()
    schema = data.get("schema")
    if not isinstance(schema, list):
        schema = data.get("dataset") or []
    cprops = data.get("customProperties")
    return ODCSContract(
        api_version=str(data.get("apiVersion", "")).strip(),
        kind=str(data.get("kind", "DataContract")).strip() or "DataContract",
        id=str(data.get("id", "")).strip(),
        version=str(data.get("version", "")).strip(),
        name=str(data.get("name", data.get("datasetName", ""))).strip(),
        status=str(data.get("status", "")).strip(),
        schema_objects=[_object_from_dict(o) for o in schema if isinstance(o, dict)],
        custom_properties=[c for c in (cprops or []) if isinstance(c, dict)],
    )


def odcs_identity_keys(contract: ODCSContract | str | Any) -> list[dict[str, Any]]:
    """The declared identity keys each schema object promises — what GoldenMatch
    should certify. One entry per (object, declared key): the composite primary key
    (`kind="primary_key"`) plus each standalone `unique` property
    (`kind="unique"`). `{object, key, kind, measures}`, where `measures` are the
    object's numeric non-key columns (the fan-out targets)."""
    contract = contract if isinstance(contract, ODCSContract) else parse_odcs_contract(contract)
    out: list[dict[str, Any]] = []
    for obj in contract.schema_objects:
        measures = obj.numeric_measures()
        pk = obj.identity_key()
        if pk:
            out.append({"object": obj.name, "key": list(pk),
                        "kind": "primary_key", "measures": list(measures)})
        for u in obj.unique_keys():
            out.append({"object": obj.name, "key": [u],
                        "kind": "unique", "measures": list(measures)})
    return out


# --- emit (produce) ----------------------------------------------------------


def emit_odcs_property(p: ODCSProperty) -> dict[str, Any]:
    out: dict[str, Any] = {"name": p.name}
    if p.logical_type:
        out["logicalType"] = p.logical_type
    if p.physical_type:
        out["physicalType"] = p.physical_type
    if p.primary_key:
        out["primaryKey"] = True
        if p.primary_key_position is not None:
            out["primaryKeyPosition"] = p.primary_key_position
    if p.unique:
        out["unique"] = True
    if p.required:
        out["required"] = True
    return out


def emit_odcs_object(o: ODCSSchemaObject) -> dict[str, Any]:
    out: dict[str, Any] = {"name": o.name, "logicalType": "object"}
    if o.physical_name:
        out["physicalName"] = o.physical_name
    out["properties"] = [emit_odcs_property(p) for p in o.properties]
    if o.quality:
        out["quality"] = list(o.quality)
    return out


def emit_odcs(contract: ODCSContract) -> dict[str, Any]:
    out: dict[str, Any] = {
        "apiVersion": contract.api_version or "v3.0.0",
        "kind": contract.kind or "DataContract",
    }
    if contract.id:
        out["id"] = contract.id
    if contract.version:
        out["version"] = contract.version
    if contract.name:
        out["name"] = contract.name
    if contract.status:
        out["status"] = contract.status
    out["schema"] = [emit_odcs_object(o) for o in contract.schema_objects]
    if contract.custom_properties:
        out["customProperties"] = list(contract.custom_properties)
    return out


def emit_odcs_yaml(contract: ODCSContract) -> str:
    """Render an `ODCSContract` as a declarative ODCS v3 doc. Round-trips through
    `parse_odcs_contract`."""
    import yaml

    return yaml.safe_dump(emit_odcs(contract), sort_keys=False, default_flow_style=False)


def emit_odcs_from_crosswalk(
    crosswalk: Any,
    *,
    contract_name: str = "resolved_identity",
    object_name: str = "resolved_entity",
    source_join_column: str | None = None,
    resolved_field: str | None = None,
    version: str = "1.0.0",
    certificate: Any = None,
) -> str:
    """Emit an ODCS v3 data contract for a `ResolvedCrosswalk` (wedge B): a schema
    object whose GoldenMatch-resolved key is declared `primaryKey: true` — so every
    consumer that honors the contract joins on resolved identity, not a raw source
    PK. GoldenMatch provenance (+ an optional key-integrity certificate) rides in the
    contract's `customProperties` as the `goldenmatch` property.
    """
    resolved_field = resolved_field or getattr(crosswalk, "resolved_key", "resolved_entity_id")
    join_col = source_join_column or resolved_field

    obj = ODCSSchemaObject(
        name=object_name,
        properties=[
            ODCSProperty(name=join_col, logical_type="string",
                         primary_key=True, primary_key_position=1, unique=True, required=True),
            ODCSProperty(name="source", logical_type="string"),
        ],
    )

    gm: dict[str, Any] = {
        "generated_by": "goldenmatch.semantic",
        "resolved_key": resolved_field,
        "n_records": getattr(crosswalk, "n_records", None),
        "n_entities": getattr(crosswalk, "n_entities", None),
        "reduction_ratio": round(getattr(crosswalk, "reduction_ratio", 0.0), 6),
    }
    if certificate is not None:
        from goldenmatch.core.key_integrity_certificate import certificate_verdict

        gm["key_integrity"] = certificate_verdict(certificate)

    contract = ODCSContract(
        api_version="v3.0.0",
        kind="DataContract",
        version=version,
        name=contract_name,
        status="draft",
        schema_objects=[obj],
        custom_properties=[{"property": "goldenmatch", "value": gm}],
    )
    return emit_odcs_yaml(contract)


# --- bridge: certify the keys an ODCS contract declares (metric-aware, wedge A) --


def certify_odcs_contract(
    contract: ODCSContract | str | Any,
    frames: dict[str, Any],
    *,
    resolve: bool = False,
    roles: Any = None,
) -> list[dict[str, Any]]:
    """For each declared identity key in a data contract, certify it against the
    data via wedge A — certifying exactly the promise the contract makes, with the
    object's **numeric properties as the fan-out measures** whose inflation a
    duplicated key would cause.

    `frames` maps a schema-object name (or its `physicalName`) to the table backing
    it (pyarrow / polars / pandas / dict). An object whose frame is absent, or which
    declares no identity key, is skipped. `resolve=True` also measures identity
    fragmentation / undercount via ER (fail-open); pass `roles` (a
    `SemanticFieldRoles`) to make that ER metric-aware — it resolves on the contract's
    descriptive columns and never on a numeric measure.

    Returns `[{object, key, kind, certificate}]` — one per declared key (the
    composite primary key plus each standalone `unique` property).
    """
    from goldenmatch.semantic.blocking import _frame_columns, metric_aware_attributes
    from goldenmatch.semantic.key_integrity import certify_key_integrity

    contract = contract if isinstance(contract, ODCSContract) else parse_odcs_contract(contract)
    out: list[dict[str, Any]] = []
    for obj in contract.schema_objects:
        df = frames.get(obj.name)
        if df is None and obj.physical_name is not None:
            df = frames.get(obj.physical_name)
        if df is None:
            continue
        cols = _frame_columns(df)
        measures = [m for m in obj.numeric_measures() if m in cols]
        attributes = (
            metric_aware_attributes(roles, cols)
            if (resolve and roles is not None) else None
        )
        for entry in odcs_identity_keys(contract):
            if entry["object"] != obj.name:
                continue
            key = entry["key"]
            if not key or any(k not in cols for k in key):
                continue
            cert = certify_key_integrity(
                df, key=key, measures=measures, resolve=resolve, attributes=attributes
            )
            out.append({
                "object": obj.name,
                "key": list(key),
                "kind": entry["kind"],
                "certificate": cert,
            })
    return out
