"""Feast (feature-store) reader + emitter — the feature-store dialect.

A feature store is a join graph too, one layer over into ML: a **`FeatureView`**
is keyed on an **`Entity`**, and the entity's **`join_keys`** are the identity the
features silently assume. The three break modes the metrics-layer wedge catches
reappear here, and bite ML directly:

- **duplicated join key -> fan-out**: an aggregated feature (`sum` / `count` over a
  not-truly-unique entity key) double-counts; a point-in-time join multiplies rows;
- **fragmented entity -> undercount / training-serving skew**: one real entity split
  across keys splits its own feature history, so the model trains on half a customer;
- **non-conformed keys across sources**: the feature view can't join to the entity
  at all.

Feast's `Entity(join_keys=[...])` maps one-to-one onto MetricFlow `entity (primary)`
and Cube `primary_key` — so exactly the same certifier applies:

- **consume** a Feast repo to learn the declared entities + the keys each feature
  view rides on (`parse_feast_models` / `parse_feast_objects`, `feast_join_keys`) —
  "what to resolve";
- **certify** (bridge to wedge A) each feature view's entity join key against the
  source data, with the view's **features as the measures** whose fan-out is
  quantified (`certify_feast_feature_views`);
- **emit** (bridge to wedge B) a Feast `Entity` + `FeatureView` declaring the
  GoldenMatch-resolved key as the entity `join_keys`, provenance in `tags`
  (`emit_feast_from_crosswalk`). `parse_feast_models(emit_feast_yaml(...))`
  round-trips.

Reads/writes the DECLARATION only (a declarative `{entities, feature_views}` doc or
duck-typed Feast SDK objects — no `feast` import in goldenmatch); it never touches a
warehouse. Library-only, advisory, parity-free (it plugs into the existing
`certify_semantic_model` dialect dispatch, adding no new surface).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from goldenmatch.semantic.metricflow import _load

# --- model -------------------------------------------------------------------


@dataclass
class FeastEntity:
    """A Feast entity — the identity abstraction. `join_keys` is THE key the
    feature views join on (Feast's modern spelling; a legacy singular `join_key`
    is normalized to a one-element list on parse)."""

    name: str
    join_keys: list[str] = field(default_factory=list)
    value_type: str = "STRING"
    description: str = ""


@dataclass
class FeastFeatureView:
    """A Feast feature view — keyed on one or more entities (by NAME). `features`
    are the field names the view serves; they are the measures whose fan-out a
    duplicated entity key inflates. `source` names the offline table backing it."""

    name: str
    entities: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    source: str | None = None
    ttl: str | None = None
    tags: dict[str, Any] | None = None


@dataclass
class FeastRepo:
    entities: list[FeastEntity] = field(default_factory=list)
    feature_views: list[FeastFeatureView] = field(default_factory=list)

    def entity_by_name(self, name: str) -> FeastEntity | None:
        for e in self.entities:
            if e.name == name:
                return e
        return None


# --- parse (consume) ---------------------------------------------------------


def _as_name_list(value: Any) -> list[str]:
    """A list of names from either `["a", "b"]` or `[{"name": "a"}, ...]` (Feast
    `schema=[Field(name=...)]` / `features=[Feature(name=...)]` both project to
    names here)."""
    out: list[str] = []
    for item in value or []:
        if isinstance(item, str):
            if item.strip():
                out.append(item.strip())
        elif isinstance(item, dict):
            n = str(item.get("name", "")).strip()
            if n:
                out.append(n)
        else:  # duck-typed SDK object with a `.name`
            n = str(getattr(item, "name", "") or "").strip()
            if n:
                out.append(n)
    return out


def _entity_join_keys(e: dict[str, Any]) -> list[str]:
    """Read `join_keys` (modern, list) falling back to a singular `join_key`."""
    jk = e.get("join_keys")
    if isinstance(jk, list) and jk:
        return [str(k).strip() for k in jk if str(k).strip()]
    single = e.get("join_key")
    if isinstance(single, str) and single.strip():
        return [single.strip()]
    # A Feast entity with neither declared defaults its join key to its own name.
    name = str(e.get("name", "")).strip()
    return [name] if name else []


def _parse_entity(e: dict[str, Any]) -> FeastEntity:
    return FeastEntity(
        name=str(e.get("name", "")).strip(),
        join_keys=_entity_join_keys(e),
        value_type=str(e.get("value_type", "STRING")),
        description=str(e.get("description", "")),
    )


def _parse_feature_view(fv: dict[str, Any]) -> FeastFeatureView:
    # Feature names live under `features` or `schema` (Feast renamed the kwarg);
    # accept either. Entity refs are names (or objects with `.name`).
    features = _as_name_list(fv.get("features")) or _as_name_list(fv.get("schema"))
    return FeastFeatureView(
        name=str(fv.get("name", "")).strip(),
        entities=_as_name_list(fv.get("entities")),
        features=features,
        source=(str(fv["source"]).strip() if fv.get("source") is not None else None),
        ttl=(str(fv["ttl"]) if fv.get("ttl") is not None else None),
        tags=fv.get("tags"),
    )


def parse_feast_models(source: str | Any) -> FeastRepo:
    """Parse a declarative Feast repo (path, YAML/JSON string, or dict) into a
    `FeastRepo`.

    Expects a top-level `entities:` list and a `feature_views:` list — a faithful
    projection of the Feast object model (`Entity` / `FeatureView`). An entity that
    declares no `join_keys` defaults its key to its own name (Feast's own default).
    """
    data = _load(source)
    entities = [_parse_entity(e) for e in (data.get("entities") or []) if isinstance(e, dict)]
    feature_views = [
        _parse_feature_view(fv) for fv in (data.get("feature_views") or []) if isinstance(fv, dict)
    ]
    return FeastRepo(entities=entities, feature_views=feature_views)


def parse_feast_objects(entities: Any, feature_views: Any) -> FeastRepo:
    """Build a `FeastRepo` from live Feast SDK objects — e.g.
    `parse_feast_objects(store.list_entities(), store.list_feature_views())`.

    Duck-typed: reads `.name` / `.join_keys` (or legacy `.join_key`) off each entity
    and `.name` / `.entities` / `.features`-or-`.schema` / `.batch_source|.source`
    off each feature view. No `feast` import — so goldenmatch never depends on it.
    """
    ents: list[FeastEntity] = []
    for e in entities or []:
        jk = getattr(e, "join_keys", None)
        if not jk:
            single = getattr(e, "join_key", None)
            jk = [single] if single else []
        name = str(getattr(e, "name", "") or "").strip()
        ents.append(FeastEntity(
            name=name,
            join_keys=[str(k).strip() for k in (jk or []) if str(k).strip()] or ([name] if name else []),
            value_type=str(getattr(e, "value_type", "STRING")),
            description=str(getattr(e, "description", "") or ""),
        ))
    fvs: list[FeastFeatureView] = []
    for fv in feature_views or []:
        ent_refs = getattr(fv, "entities", None) or []
        # Feast feature views reference entities by name (str) or by Entity object.
        ent_names = [r if isinstance(r, str) else str(getattr(r, "name", "") or "") for r in ent_refs]
        feats = _as_name_list(getattr(fv, "features", None)) or _as_name_list(getattr(fv, "schema", None))
        src = getattr(fv, "batch_source", None) or getattr(fv, "source", None)
        src_name = str(getattr(src, "name", "") or "").strip() or (str(src).strip() if src else None)
        fvs.append(FeastFeatureView(
            name=str(getattr(fv, "name", "") or "").strip(),
            entities=[n.strip() for n in ent_names if n.strip()],
            features=feats,
            source=src_name,
            tags=getattr(fv, "tags", None),
        ))
    return FeastRepo(entities=ents, feature_views=fvs)


def feast_join_keys(repo: FeastRepo | str | Any) -> list[dict[str, Any]]:
    """The keys each feature view rides on — the identity its features assume. One
    entry per (feature_view, entity): `{feature_view, entity, key, features,
    source}`, where `key` is the entity's resolved `join_keys`. This is what
    GoldenMatch should resolve / certify (metric-aware)."""
    repo = repo if isinstance(repo, FeastRepo) else parse_feast_models(repo)
    out: list[dict[str, Any]] = []
    for fv in repo.feature_views:
        for ent_name in fv.entities:
            ent = repo.entity_by_name(ent_name)
            key = ent.join_keys if ent is not None else [ent_name]
            out.append({
                "feature_view": fv.name,
                "entity": ent_name,
                "key": list(key),
                "features": list(fv.features),
                "source": fv.source,
            })
    return out


# --- emit (produce) ----------------------------------------------------------


def emit_feast_entity(e: FeastEntity) -> dict[str, Any]:
    out: dict[str, Any] = {"name": e.name, "join_keys": list(e.join_keys)}
    if e.value_type:
        out["value_type"] = e.value_type
    if e.description:
        out["description"] = e.description
    return out


def emit_feast_feature_view(fv: FeastFeatureView) -> dict[str, Any]:
    out: dict[str, Any] = {"name": fv.name, "entities": list(fv.entities)}
    if fv.features:
        out["features"] = list(fv.features)
    if fv.source is not None:
        out["source"] = fv.source
    if fv.ttl is not None:
        out["ttl"] = fv.ttl
    if fv.tags:
        out["tags"] = fv.tags
    return out


def emit_feast(repo: FeastRepo) -> dict[str, Any]:
    return {
        "entities": [emit_feast_entity(e) for e in repo.entities],
        "feature_views": [emit_feast_feature_view(fv) for fv in repo.feature_views],
    }


def emit_feast_yaml(repo: FeastRepo) -> str:
    """Render a `FeastRepo` as a declarative Feast doc (`entities:` +
    `feature_views:`). Round-trips through `parse_feast_models`."""
    import yaml

    return yaml.safe_dump(emit_feast(repo), sort_keys=False, default_flow_style=False)


def emit_feast_from_crosswalk(
    crosswalk: Any,
    *,
    feature_view: str,
    source_join_column: str | None = None,
    entity_name: str = "resolved_entity",
    resolved_field: str | None = None,
    features: list[str] | None = None,
    certificate: Any = None,
) -> str:
    """Emit a declarative Feast doc for a `ResolvedCrosswalk` (wedge B): an `Entity`
    whose `join_keys` is the GoldenMatch-resolved key, plus a `FeatureView` keyed on
    it — so every feature is served against resolved identity, not a raw source PK.
    GoldenMatch provenance (+ an optional key-integrity certificate) rides in the
    feature view's `tags.goldenmatch`.
    """
    resolved_field = resolved_field or getattr(crosswalk, "resolved_key", "resolved_entity_id")
    join_col = source_join_column or resolved_field

    ent = FeastEntity(name=entity_name, join_keys=[join_col], value_type="STRING")

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

    fv = FeastFeatureView(
        name=feature_view,
        entities=[entity_name],
        features=list(features or []),
        source=getattr(crosswalk, "source", None) or feature_view,
        tags={"goldenmatch": gm},
    )
    return emit_feast_yaml(FeastRepo(entities=[ent], feature_views=[fv]))


# --- bridge: certify the keys a Feast repo's feature views join on (wedge A) ---


def certify_feast_feature_views(
    repo: FeastRepo | str | Any,
    frames: dict[str, Any],
    *,
    resolve: bool = False,
    roles: Any = None,
) -> list[dict[str, Any]]:
    """For each feature view, certify the entity `join_keys` it rides on via wedge A
    — certifying exactly the identity the features depend on, with the view's
    **features as the measures** whose fan-out is quantified (a duplicated join key
    inflates every aggregated feature).

    `frames` maps a feature-view name (or its `source` name) to the table backing it
    (pyarrow / polars / pandas / dict). A view whose frame is absent, or whose entity
    declares no join key, is skipped. `resolve=True` also measures entity
    fragmentation / undercount via ER (fail-open); pass `roles` (a
    `SemanticFieldRoles`) to make that ER metric-aware — it resolves on descriptive
    columns and never on a feature value (a churn score is not identity evidence).

    Returns `[{feature_view, entity, key, certificate}]`.
    """
    from goldenmatch.semantic.blocking import _frame_columns, metric_aware_attributes
    from goldenmatch.semantic.key_integrity import certify_key_integrity

    repo = repo if isinstance(repo, FeastRepo) else parse_feast_models(repo)
    out: list[dict[str, Any]] = []
    for jk in feast_join_keys(repo):
        key = jk["key"]
        if not key:
            continue
        df = frames.get(jk["feature_view"])
        if df is None and jk["source"] is not None:
            df = frames.get(jk["source"])
        if df is None:
            continue
        cols = _frame_columns(df)
        # features present in the frame are the fan-out measures.
        measures = [f for f in jk["features"] if f in cols]
        attributes = (
            metric_aware_attributes(roles, cols)
            if (resolve and roles is not None) else None
        )
        cert = certify_key_integrity(
            df, key=key, measures=measures, resolve=resolve, attributes=attributes
        )
        out.append({
            "feature_view": jk["feature_view"],
            "entity": jk["entity"],
            "key": list(key),
            "certificate": cert,
        })
    return out
